"""Initial Key Hunt schema."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "playlists",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("youtube_playlist_id", sa.String(128), nullable=False),
        sa.Column("source_url", sa.String(2048), nullable=False),
        sa.Column("title", sa.String(512)),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_playlists_youtube_playlist_id", "playlists", ["youtube_playlist_id"], unique=True
    )
    op.create_table(
        "tracks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("youtube_video_id", sa.String(32), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("artist", sa.String(512)),
        sa.Column("source_url", sa.String(2048), nullable=False),
        sa.Column("thumbnail_url", sa.String(2048)),
        sa.Column("duration_seconds", sa.Integer()),
        sa.Column("audio_path", sa.String(512)),
        sa.Column("audio_status", sa.String(11), nullable=False),
        sa.Column("detected_key", sa.String(8)),
        sa.Column("detected_scale", sa.String(7)),
        sa.Column("analysis_confidence", sa.Float()),
        sa.Column("analysis_version", sa.String(128)),
        sa.Column("analyzed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tracks_youtube_video_id", "tracks", ["youtube_video_id"], unique=True)
    op.create_index("ix_tracks_audio_status", "tracks", ["audio_status"])
    op.create_table(
        "playlist_tracks",
        sa.Column(
            "playlist_id",
            sa.String(36),
            sa.ForeignKey("playlists.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "track_id",
            sa.String(36),
            sa.ForeignKey("tracks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("position", sa.Integer()),
        sa.UniqueConstraint("playlist_id", "track_id"),
    )
    op.create_table(
        "game_rounds",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("player_id", sa.String(36), nullable=False),
        sa.Column("playlist_id", sa.String(36), sa.ForeignKey("playlists.id"), nullable=False),
        sa.Column("track_id", sa.String(36), sa.ForeignKey("tracks.id"), nullable=False),
        sa.Column("status", sa.String(11), nullable=False),
        sa.Column("revealed_at", sa.DateTime(timezone=True)),
        sa.Column("result", sa.String(9)),
        sa.Column("answered_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_game_rounds_player_id", "game_rounds", ["player_id"])
    op.create_index("ix_game_rounds_playlist_id", "game_rounds", ["playlist_id"])
    op.create_index("ix_game_rounds_track_id", "game_rounds", ["track_id"])
    op.create_index("ix_game_rounds_status", "game_rounds", ["status"])
    op.create_index("ix_game_rounds_player_created", "game_rounds", ["player_id", "created_at"])
    op.create_index("ix_game_rounds_status_updated", "game_rounds", ["status", "updated_at"])


def downgrade() -> None:
    op.drop_table("game_rounds")
    op.drop_table("playlist_tracks")
    op.drop_table("tracks")
    op.drop_table("playlists")
