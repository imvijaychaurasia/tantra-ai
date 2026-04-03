"""Add youtube_videos table (Phase 3a)

Revision ID: 001_youtube_videos
Revises:
Create Date: 2026-04-03

Adds the YouTubeVideo table that tracks YouTube content through the full
production pipeline: scripted → approved → producing → produced → uploading → live.

The table is also created automatically by Base.metadata.create_all() on
worker startup (celery_app.py @worker_ready signal), so this migration is
for clean schema management in production / CI environments.

Run: docker compose exec tantra-api alembic upgrade head
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "001_youtube_videos"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "youtube_videos",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),

        # Planning layer links (both nullable — video may be ad-hoc)
        sa.Column("agent_task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=True),

        # Script fields (populated by YouTubeCrew)
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("script", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("thumbnail_concept", sa.Text(), nullable=True),
        sa.Column("tags", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("topic_hint", sa.Text(), nullable=True),

        # Status state machine
        sa.Column("status", sa.String(20), nullable=False, server_default="scripted"),

        # Production file paths (populated by produce_youtube_video — Phase 3b)
        sa.Column("audio_path", sa.Text(), nullable=True),
        sa.Column("video_path", sa.Text(), nullable=True),
        sa.Column("thumbnail_path", sa.Text(), nullable=True),

        # Upload result (populated by upload_youtube_video — Phase 3c)
        sa.Column("youtube_video_id", sa.String(50), nullable=True),
        sa.Column("youtube_url", sa.String(200), nullable=True),

        # n8n tracking
        sa.Column("n8n_execution_id", sa.String(100), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),

        # Analytics (updated by youtube_analytics_pull)
        sa.Column("views", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("likes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("comments", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("analytics_updated_at", sa.DateTime(), nullable=True),

        # Timestamps
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("produced_at", sa.DateTime(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(), nullable=True),

        # Constraints
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["agent_task_id"], ["agent_tasks.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"], ["weekly_plans.id"], ondelete="SET NULL"
        ),
    )

    # Indexes
    op.create_index("ix_youtube_videos_status", "youtube_videos", ["status"])
    op.create_index("ix_youtube_videos_agent_task_id", "youtube_videos", ["agent_task_id"])
    op.create_index("ix_youtube_videos_plan_id", "youtube_videos", ["plan_id"])


def downgrade() -> None:
    op.drop_index("ix_youtube_videos_plan_id", table_name="youtube_videos")
    op.drop_index("ix_youtube_videos_agent_task_id", table_name="youtube_videos")
    op.drop_index("ix_youtube_videos_status", table_name="youtube_videos")
    op.drop_table("youtube_videos")
