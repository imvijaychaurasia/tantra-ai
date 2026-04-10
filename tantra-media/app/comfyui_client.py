"""
tantra-media — ComfyUI REST client
तंत्र  ·  Flux.1-dev text-to-image generation for visual_video scenes

Submits a Flux.1-dev workflow to a running ComfyUI server and returns a PIL
Image.  Falls back gracefully (returns None) if ComfyUI is unreachable or
generation fails — produce.py will then use the Pillow fallback pipeline.

ComfyUI API reference:
  POST /prompt           — submit a workflow
  GET  /history/{id}     — poll completion status
  GET  /view?filename=…  — download output image

Flux.1-dev model layout (ComfyUI models/ directory):
  diffusion_models/flux1-dev.safetensors   (UNET — 24 GB fp16, or use fp8 ~12 GB)
  vae/ae.safetensors                       (Flux VAE)
  clip/t5xxl_fp8_e4m3fn.safetensors        (T5 text encoder fp8)
  clip/clip_l.safetensors                  (CLIP-L text encoder)

VRAM budget (RTX 5070 Ti 16 GB):
  Flux.1-dev fp8 UNET + VAE + T5 fp8 ≈ 14–15 GB  → fits if Ollama is idle
  Resolution 1280×720  → safe
  Resolution 1920×1080 → borderline; use 1280×720 and upscale

Environment variables (all optional — sane defaults shown):
  COMFYUI_URL       http://tantra-comfyui:8188
  COMFYUI_MODEL     flux1-dev.safetensors
  COMFYUI_CLIP_T5   t5xxl_fp8_e4m3fn.safetensors
  COMFYUI_CLIP_L    clip_l.safetensors
  COMFYUI_VAE       ae.safetensors
  COMFYUI_STEPS     20          — inference steps (20 is fine for Flux)
  COMFYUI_WIDTH     1280        — generation width  (upscaled to 1920 by compositor)
  COMFYUI_HEIGHT    720         — generation height (upscaled to 1080 by compositor)
  COMFYUI_TIMEOUT   180         — seconds to wait per image
  COMFYUI_ENABLED   true        — set to false to force Pillow fallback globally
"""
from __future__ import annotations

import io
import logging
import os
import random
import time
import uuid
from typing import Optional

import httpx
from PIL import Image

log = logging.getLogger("tantra-media.comfyui")

# ── Config ─────────────────────────────────────────────────────────────────
COMFYUI_URL     = os.getenv("COMFYUI_URL",     "http://tantra-comfyui:8188")
COMFYUI_MODEL   = os.getenv("COMFYUI_MODEL",   "flux1-dev.safetensors")
COMFYUI_CLIP_T5 = os.getenv("COMFYUI_CLIP_T5", "t5xxl_fp8_e4m3fn.safetensors")
COMFYUI_CLIP_L  = os.getenv("COMFYUI_CLIP_L",  "clip_l.safetensors")
COMFYUI_VAE     = os.getenv("COMFYUI_VAE",     "ae.safetensors")
COMFYUI_STEPS   = int(os.getenv("COMFYUI_STEPS",   "20"))
COMFYUI_WIDTH   = int(os.getenv("COMFYUI_WIDTH",   "1280"))
COMFYUI_HEIGHT  = int(os.getenv("COMFYUI_HEIGHT",  "720"))
COMFYUI_TIMEOUT = int(os.getenv("COMFYUI_TIMEOUT", "180"))
COMFYUI_ENABLED = os.getenv("COMFYUI_ENABLED", "true").lower() not in ("false", "0", "no")


# ── Workflow builder ───────────────────────────────────────────────────────

def _build_flux_workflow(
    prompt: str,
    width: int = COMFYUI_WIDTH,
    height: int = COMFYUI_HEIGHT,
    steps: int = COMFYUI_STEPS,
    seed: int = -1,
) -> dict:
    """
    Build a ComfyUI API-format workflow dict for Flux.1-dev text-to-image.

    Node map:
      1  UNETLoader          — loads Flux UNET (fp8)
      2  VAELoader            — loads Flux VAE
      3  DualCLIPLoader       — loads T5-XXL + CLIP-L
      4  CLIPTextEncode       — encodes the prompt
      5  EmptySD3LatentImage  — creates blank latent at target resolution
      6  ModelSamplingFlux    — Flux-specific sampler conditioning
      7  RandomNoise          — seed
      8  BasicGuider          — connects model + conditioning
      9  BasicScheduler       — beta scheduler, N steps
      10 SamplerCustomAdvanced — runs the diffusion
      11 KSamplerSelect       — euler sampler
      12 VAEDecode             — latent → pixels
      13 SaveImage             — writes PNG to ComfyUI output dir
    """
    if seed < 0:
        seed = random.randint(0, 2 ** 32 - 1)

    return {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": COMFYUI_MODEL,
                "weight_dtype": "fp8_e4m3fn",
            },
        },
        "2": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": COMFYUI_VAE},
        },
        "3": {
            "class_type": "DualCLIPLoader",
            "inputs": {
                "clip_name1": COMFYUI_CLIP_T5,
                "clip_name2": COMFYUI_CLIP_L,
                "type": "flux",
            },
        },
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "clip": ["3", 0],
                "text": prompt,
            },
        },
        "5": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {
                "width": width,
                "height": height,
                "batch_size": 1,
            },
        },
        "6": {
            "class_type": "ModelSamplingFlux",
            "inputs": {
                "model": ["1", 0],
                "max_shift": 1.15,
                "base_shift": 0.5,
                "width": width,
                "height": height,
            },
        },
        "7": {
            "class_type": "RandomNoise",
            "inputs": {"noise_seed": seed},
        },
        "8": {
            "class_type": "BasicGuider",
            "inputs": {
                "model": ["6", 0],
                "conditioning": ["4", 0],
            },
        },
        "9": {
            "class_type": "BasicScheduler",
            "inputs": {
                "model": ["1", 0],
                "scheduler": "beta",
                "steps": steps,
                "denoise": 1.0,
            },
        },
        "10": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["7", 0],
                "guider": ["8", 0],
                "sampler": ["11", 0],
                "sigmas": ["9", 0],
                "latent_image": ["5", 0],
            },
        },
        "11": {
            "class_type": "KSamplerSelect",
            "inputs": {"sampler_name": "euler"},
        },
        "12": {
            "class_type": "VAEDecode",
            "inputs": {
                "samples": ["10", 0],
                "vae": ["2", 0],
            },
        },
        "13": {
            "class_type": "SaveImage",
            "inputs": {
                "images": ["12", 0],
                "filename_prefix": "tantra_bg",
            },
        },
    }


# ── Client ─────────────────────────────────────────────────────────────────

class ComfyUIClient:
    """
    Thin HTTP client for the ComfyUI REST API.

    Thread-safe — each call creates its own httpx.Client context.
    Non-fatal: every public method returns None / False on failure rather
    than raising, so produce.py can fall back to the Pillow pipeline.
    """

    def __init__(self, base_url: str = COMFYUI_URL):
        self.base_url = base_url.rstrip("/")
        self.client_id = str(uuid.uuid4())[:8]   # short random ID for this session

    # ── Availability ──────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """
        Quick availability probe.  Returns True if ComfyUI is up and reachable.
        Uses a 5-second connection timeout — fast enough not to stall a Celery task.
        """
        if not COMFYUI_ENABLED:
            log.debug("ComfyUI disabled via COMFYUI_ENABLED=false")
            return False
        try:
            resp = httpx.get(f"{self.base_url}/system_stats", timeout=5.0)
            ok = resp.status_code == 200
            if not ok:
                log.warning("ComfyUI /system_stats returned HTTP %d", resp.status_code)
            return ok
        except Exception as exc:
            log.debug("ComfyUI not available (%s: %s)", type(exc).__name__, exc)
            return False

    # ── Generation ────────────────────────────────────────────────────────

    def generate_image(
        self,
        prompt: str,
        width: int = COMFYUI_WIDTH,
        height: int = COMFYUI_HEIGHT,
        steps: int = COMFYUI_STEPS,
        seed: int = -1,
    ) -> Optional[Image.Image]:
        """
        Submit a Flux.1-dev generation job and return the PIL Image (RGB mode).

        Pipeline:
          1. POST /prompt      — submit workflow JSON
          2. GET  /history/id  — poll every 3 s until status shows output images
          3. GET  /view        — download the PNG bytes
          4. Return PIL.Image

        Returns None if any step fails (ComfyUI down, timeout, download error).
        The caller (produce.py) will fall back to the Pillow slideshow renderer.

        Args:
            prompt:  Full text prompt including cinematic style keywords.
            width:   Latent width  (1280 recommended for 16 GB VRAM).
            height:  Latent height (720  recommended for 16 GB VRAM).
            steps:   Diffusion steps (20 is fast + good quality for Flux).
            seed:    Fixed seed for reproducibility; -1 = random.

        Returns:
            PIL.Image in RGB mode, or None on failure.
        """
        workflow = _build_flux_workflow(prompt, width, height, steps, seed)
        log.info(
            "ComfyUI generate: %dx%d, %d steps | %s…",
            width, height, steps, prompt[:80],
        )

        # ── Submit ─────────────────────────────────────────────────────────
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(
                    f"{self.base_url}/prompt",
                    json={"prompt": workflow, "client_id": self.client_id},
                )
                resp.raise_for_status()
                prompt_id: str = resp.json()["prompt_id"]
        except Exception as exc:
            log.warning("ComfyUI submit failed: %s", exc)
            return None

        log.debug("ComfyUI prompt_id=%s — polling…", prompt_id)

        # ── Poll ───────────────────────────────────────────────────────────
        deadline = time.time() + COMFYUI_TIMEOUT
        poll_interval = 3.0
        while time.time() < deadline:
            time.sleep(poll_interval)
            try:
                with httpx.Client(timeout=15.0) as client:
                    hist_resp = client.get(f"{self.base_url}/history/{prompt_id}")
                if hist_resp.status_code != 200:
                    continue
                hist = hist_resp.json()
                if prompt_id not in hist:
                    continue  # still queued / running

                # Check for error
                status = hist[prompt_id].get("status", {})
                if status.get("status_str") == "error":
                    log.error(
                        "ComfyUI generation error for prompt_id=%s: %s",
                        prompt_id, status.get("messages", "?"),
                    )
                    return None

                # Find SaveImage output
                outputs = hist[prompt_id].get("outputs", {})
                for _node_id, node_out in outputs.items():
                    images = node_out.get("images", [])
                    if not images:
                        continue
                    img_info = images[0]
                    # Download PNG
                    try:
                        with httpx.Client(timeout=30.0) as client:
                            img_resp = client.get(
                                f"{self.base_url}/view",
                                params={
                                    "filename": img_info["filename"],
                                    "subfolder": img_info.get("subfolder", ""),
                                    "type": img_info.get("type", "output"),
                                },
                            )
                            img_resp.raise_for_status()
                        pil_img = Image.open(io.BytesIO(img_resp.content)).convert("RGB")
                        log.info(
                            "ComfyUI ✓ %s (%dx%d)",
                            img_info["filename"], pil_img.width, pil_img.height,
                        )
                        return pil_img
                    except Exception as dl_exc:
                        log.warning("ComfyUI image download failed: %s", dl_exc)
                        return None

            except Exception as poll_exc:
                log.debug("ComfyUI poll error: %s", poll_exc)
                # Transient error — keep trying until deadline

        log.warning(
            "ComfyUI generation timed out after %ds (prompt_id=%s)",
            COMFYUI_TIMEOUT, prompt_id,
        )
        return None


# ── Scene prompt builder ───────────────────────────────────────────────────

def build_scene_bg_prompt(
    scene: dict,
    video_title: str = "",
    video_type: str = "visual_video",
) -> str:
    """
    Build a Flux-optimised background generation prompt from a script scene.

    Takes the scene's visual_prompt and enriches it with cinematic style
    keywords that work well with Flux.1-dev for tech/educational YouTube content.

    The prompt deliberately excludes text, watermarks, and UI elements since
    the Pillow compositor layer will overlay those on top of the background.

    Args:
        scene:       Scene dict from script JSON.
        video_title: Overall video title for contextual hints.
        video_type:  Scene video type (used for style hints).

    Returns:
        Full prompt string ready to pass to ComfyUIClient.generate_image().
    """
    visual_prompt = scene.get("visual_prompt", "").strip()
    scene_type    = scene.get("type", "content").lower()

    # Style prefix — dark tech aesthetic consistent with the Pillow slideshow theme
    style_prefix = (
        "cinematic dark tech background, deep space blue and cyan tones, "
        "dramatic lighting, professional photography, ultra detailed, 8k, "
        "no text, no watermarks, no UI elements, no people"
    )

    # Scene-type flavour
    type_suffix = {
        "hook":    "dynamic energy, motion blur, dramatic reveal composition",
        "content": "clean sharp focus, data visualization aesthetic, blueprint style",
        "cta":     "inspiring upward composition, glowing particles, success feeling",
        "outro":   "fade horizon, calm resolution, subtle grid lines",
    }.get(scene_type, "professional tech atmosphere")

    if not visual_prompt:
        visual_prompt = f"technology concept related to {video_title}" if video_title else "abstract tech landscape"

    return f"{style_prefix}, {visual_prompt}, {type_suffix}"
