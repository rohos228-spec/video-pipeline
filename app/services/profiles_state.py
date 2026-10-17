from datetime import datetime
from sqlalchemy import JSON,String,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column
from app.models import Base

class ProfileOverlay(Base):
    __tablename__='node_profile_overlays'
    __table_args__=(UniqueConstraint('execution_id','node_key',name='uq_one_node_profile'),)
    overlay_id: Mapped[str]=mapped_column(String(36),primary_key=True)
    execution_id: Mapped[str]=mapped_column(String(36),index=True)
    project_id: Mapped[int]=mapped_column(index=True)
    node_key: Mapped[str]=mapped_column(String(60))
    source_attempt_id: Mapped[str]=mapped_column(String(36))
    attempt_id: Mapped[str | None]=mapped_column(String(36),default=None)
    profile_id: Mapped[str]=mapped_column(String(80))
    base_version: Mapped[str]=mapped_column(String(64))
    graph_version: Mapped[str]=mapped_column(String(64))
    catalog_hash: Mapped[str]=mapped_column(String(64))
    candidate_hash: Mapped[str]=mapped_column(String(64))
    candidate: Mapped[dict]=mapped_column(JSON)
    source_body_hash: Mapped[str]=mapped_column(String(64))
    expected_body_hash: Mapped[str]=mapped_column(String(64))
    initiator: Mapped[str]=mapped_column(String(120))
    rationale: Mapped[str]=mapped_column(String(1000))
    status: Mapped[str]=mapped_column(String(24),default='approved')
    history: Mapped[list]=mapped_column(JSON,default=list)
    created_at: Mapped[datetime]=mapped_column(default=datetime.utcnow)
