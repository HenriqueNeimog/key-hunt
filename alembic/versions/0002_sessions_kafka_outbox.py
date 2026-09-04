"""Add persistent game sessions, outbox, idempotency, and media claims."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_sessions_kafka_outbox"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tracks", sa.Column("processing_claim_token", sa.String(36)))
    op.add_column("tracks", sa.Column("processing_claim_until", sa.DateTime(timezone=True)))
    op.create_index("ix_tracks_processing_claim_token", "tracks", ["processing_claim_token"])

    op.create_table(
        "game_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("player_id", sa.String(36), nullable=False),
        sa.Column("playlist_id", sa.String(36), sa.ForeignKey("playlists.id"), nullable=False),
        sa.Column("status", sa.String(9), nullable=False),
        sa.Column("current_position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("shuffle_seed", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_game_sessions_player_id", "game_sessions", ["player_id"])
    op.create_index("ix_game_sessions_playlist_id", "game_sessions", ["playlist_id"])
    op.create_index("ix_game_sessions_status", "game_sessions", ["status"])
    op.create_index("ix_game_sessions_player_created", "game_sessions", ["player_id", "created_at"])

    with op.batch_alter_table("game_rounds") as batch:
        batch.add_column(sa.Column("game_session_id", sa.String(36)))
        batch.add_column(sa.Column("session_position", sa.Integer()))
        batch.create_foreign_key(
            "fk_game_rounds_game_session_id",
            "game_sessions",
            ["game_session_id"],
            ["id"],
        )
        batch.create_index("ix_game_rounds_game_session_id", ["game_session_id"])

    op.create_table(
        "game_session_tracks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "game_session_id",
            sa.String(36),
            sa.ForeignKey("game_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("track_id", sa.String(36), sa.ForeignKey("tracks.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("preparation_status", sa.String(11), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_code", sa.String(64)),
        sa.Column("enqueued_at", sa.DateTime(timezone=True)),
        sa.Column("ready_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("game_session_id", "position"),
        sa.UniqueConstraint("game_session_id", "track_id"),
    )
    op.create_index(
        "ix_game_session_tracks_game_session_id", "game_session_tracks", ["game_session_id"]
    )
    op.create_index("ix_game_session_tracks_track_id", "game_session_tracks", ["track_id"])
    op.create_index(
        "ix_game_session_tracks_preparation_status", "game_session_tracks", ["preparation_status"]
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("topic", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("aggregate_id", sa.String(36), nullable=False),
        sa.Column(
            "game_session_id", sa.String(36), sa.ForeignKey("game_sessions.id"), nullable=False
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("dedupe_key", sa.String(255), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index("ix_outbox_events_aggregate_id", "outbox_events", ["aggregate_id"])
    op.create_index("ix_outbox_events_game_session_id", "outbox_events", ["game_session_id"])
    op.create_index("ix_outbox_events_status", "outbox_events", ["status"])
    op.create_index("ix_outbox_dispatch", "outbox_events", ["status", "available_at", "created_at"])

    op.create_table(
        "processed_events",
        sa.Column("event_id", sa.String(36), primary_key=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "session_advances",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "game_session_id",
            sa.String(36),
            sa.ForeignKey("game_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("resulting_round_id", sa.String(36), sa.ForeignKey("game_rounds.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("game_session_id", "idempotency_key"),
    )
    op.create_index("ix_session_advances_game_session_id", "session_advances", ["game_session_id"])


def downgrade() -> None:
    op.drop_table("session_advances")
    op.drop_table("processed_events")
    op.drop_table("outbox_events")
    op.drop_table("game_session_tracks")
    with op.batch_alter_table("game_rounds") as batch:
        batch.drop_index("ix_game_rounds_game_session_id")
        batch.drop_constraint("fk_game_rounds_game_session_id", type_="foreignkey")
        batch.drop_column("session_position")
        batch.drop_column("game_session_id")
    op.drop_table("game_sessions")
    op.drop_index("ix_tracks_processing_claim_token", table_name="tracks")
    op.drop_column("tracks", "processing_claim_until")
    op.drop_column("tracks", "processing_claim_token")
