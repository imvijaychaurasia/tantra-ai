"""
Tantra AI — Skills Engine
तंत्र · AgentSkills-compatible skill system

A skill is a directory containing a SKILL.md file with YAML frontmatter.
Skills are composable, versioned, and publishable to the TantraHub registry.

Compatible with: OpenClaw AgentSkills spec, ClawHub format, Claude/Cowork skills.
"""
from tantra.skills.loader import SkillLoader, Skill
from tantra.skills.registry import SkillRegistry

__all__ = ["SkillLoader", "Skill", "SkillRegistry"]
