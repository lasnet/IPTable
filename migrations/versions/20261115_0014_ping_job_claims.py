"""Enforce one active ping job per project and identify each claim."""

from alembic import op
import sqlalchemy as sa


revision = "20261115_0014"
down_revision = "20261112_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ping_jobs", sa.Column("claim_token", sa.String(32), nullable=True))
    # Keep history and retain the earliest job, preferring a running job when present.
    op.execute("""
        WITH ranked AS (
            SELECT id, row_number() OVER (
                PARTITION BY project_id
                ORDER BY CASE WHEN status = 'running' THEN 0 ELSE 1 END, run_after, id
            ) AS rank
            FROM ping_jobs WHERE status IN ('queued', 'running')
        )
        UPDATE ping_jobs
        SET status = 'failed', error = 'Duplicate active job superseded during upgrade',
            finished_at = CURRENT_TIMESTAMP, lease_expires_at = NULL
        WHERE id IN (SELECT id FROM ranked WHERE rank > 1)
    """)
    # Runtime processes are stopped during upgrades. Recover the retained pre-upgrade claim.
    op.execute("""
        UPDATE ping_jobs SET status = 'queued', started_at = NULL, finished_at = NULL,
            lease_expires_at = NULL, error = 'Requeued during upgrade'
        WHERE status = 'running'
    """)
    active = sa.text("status IN ('queued', 'running')")
    op.create_index("uq_ping_jobs_active_project", "ping_jobs", ["project_id"], unique=True,
                    postgresql_where=active, sqlite_where=active)


def downgrade() -> None:
    op.drop_index("uq_ping_jobs_active_project", table_name="ping_jobs")
    op.drop_column("ping_jobs", "claim_token")
