from datetime import datetime
from sqlalchemy import Boolean, DateTime, JSON, String, UniqueConstraint,select
from sqlalchemy.orm import Mapped, mapped_column
from app.models import Base


class DecisionControl(Base):
    __tablename__='decision_execution_controls'
    execution_id: Mapped[str]=mapped_column(String(36),primary_key=True)
    project_id: Mapped[int]=mapped_column(index=True)
    enabled: Mapped[bool]=mapped_column(Boolean,default=False)
    initiator: Mapped[str]=mapped_column(String(120))
    rationale: Mapped[str]=mapped_column(String(1000))
    updated_at: Mapped[datetime]=mapped_column(default=datetime.utcnow)


class DecisionRecord(Base):
    __tablename__='decision_records'
    __table_args__=(UniqueConstraint('execution_id','incident_id',name='uq_decision_incident'),)
    decision_id: Mapped[str]=mapped_column(String(36),primary_key=True)
    execution_id: Mapped[str]=mapped_column(String(36),index=True)
    project_id: Mapped[int]=mapped_column(index=True)
    incident_id: Mapped[str]=mapped_column(String(36))
    node_key: Mapped[str]=mapped_column(String(60))
    source_attempt_id: Mapped[str]=mapped_column(String(36))
    context_version: Mapped[str]=mapped_column(String(64))
    status: Mapped[str]=mapped_column(String(24),default='ready')
    model_steps: Mapped[int]=mapped_column(default=0)
    lease_token: Mapped[str | None]=mapped_column(String(36),default=None)
    lease_until: Mapped[datetime | None]=mapped_column(DateTime,default=None)
    action_id: Mapped[str | None]=mapped_column(String(36),default=None)
    proposal: Mapped[dict]=mapped_column(JSON,default=dict)
    last_signature: Mapped[str | None]=mapped_column(String(64),default=None)
    policy_result: Mapped[dict]=mapped_column(JSON,default=dict)
    verification: Mapped[dict]=mapped_column(JSON,default=dict)
    created_at: Mapped[datetime]=mapped_column(default=datetime.utcnow)


class DecisionTurn(Base):
    __tablename__='decision_turns'
    __table_args__=(UniqueConstraint('decision_id','turn_key',name='uq_decision_turn'),)
    id: Mapped[int]=mapped_column(primary_key=True)
    decision_id: Mapped[str]=mapped_column(String(36),index=True)
    turn_key: Mapped[str]=mapped_column(String(160))
    role: Mapped[str]=mapped_column(String(32))
    payload: Mapped[dict]=mapped_column(JSON)
    created_at: Mapped[datetime]=mapped_column(default=datetime.utcnow)


async def authorize_recovery(session,execution_id,incident_id,decision_id):
    control=await session.get(DecisionControl,execution_id)
    owner=await session.scalar(select(DecisionRecord).where(DecisionRecord.execution_id==execution_id,
        DecisionRecord.incident_id==incident_id))
    if not owner and (not control or not control.enabled):return
    record=await session.get(DecisionRecord,decision_id) if decision_id else None
    if not control or not control.enabled or not record or record.execution_id!=execution_id or record.incident_id!=incident_id or record.status!='acting':
        raise ValueError('incident is held by its decision owner; no competing recovery')
