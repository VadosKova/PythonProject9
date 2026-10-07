import os
from contextlib import asynccontextmanager
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Boolean, Float, Integer, String, create_engine, select, Enum as SQLEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
import enum



load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. Create a .env file from .env "
        "and paste your Neon connection string there."
    )


if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgresql://", "postgresql+psycopg://", 1
    )
elif DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgres://", "postgresql+psycopg://", 1
    )


engine_options: dict = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    engine_options["connect_args"] = {"check_same_thread": False}
else:
    engine_options["pool_recycle"] = 300

engine = create_engine(DATABASE_URL, **engine_options)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)



class Base(DeclarativeBase):
    pass


class ContractStatus(str, enum.Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Mercenary(Base):
    __tablename__ = "mercenaries"

    id: Mapped[int] = mapped_column(primary_key=True)
    nickname: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    level: Mapped[int] = mapped_column(Integer, default=1)
    reputation: Mapped[int] = mapped_column(Integer, default=0)
    balance: Mapped[float] = mapped_column(Float, default=0.0)


class Contract(Base):
    __tablename__ = "contracts"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(120), index=True)
    description: Mapped[str] = mapped_column(String(500))
    difficulty: Mapped[int] = mapped_column(Integer)
    reward: Mapped[float] = mapped_column(Float)
    district: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[ContractStatus] = mapped_column(
        SQLEnum(ContractStatus), default=ContractStatus.OPEN, index=True
    )
    mercenary_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class MercenaryCreate(BaseModel):
    nickname: str = Field(min_length=2, max_length=80)
    level: int = Field(default=1, ge=1, le=10)
    reputation: int = Field(default=0, ge=0)
    balance: float = Field(default=0.0, ge=0)


class MercenaryRead(BaseModel):
    id: int
    nickname: str
    level: int
    reputation: int
    balance: float

    model_config = ConfigDict(from_attributes=True)

class ContractCreate(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    description: str = Field(min_length=5, max_length=500)
    difficulty: int = Field(ge=1, le=10)
    reward: float = Field(gt=0)
    district: str = Field(min_length=2, max_length=80)


class ContractUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, min_length=5, max_length=500)
    difficulty: int | None = Field(default=None, ge=1, le=10)
    reward: float | None = Field(default=None, gt=0)
    district: str | None = Field(default=None, min_length=2, max_length=80)


class ContractRead(BaseModel):
    id: int
    title: str
    description: str
    difficulty: int
    reward: float
    district: str
    status: ContractStatus
    mercenary_id: int | None

    model_config = ConfigDict(from_attributes=True)


class TakeContractRequest(BaseModel):
    mercenary_id: int


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DbSession = Annotated[Session, Depends(get_db)]



@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="MERCNET - Биржа контрактов для наёмников",
    description=("Закрытая сеть для публикации опасных контрактов и управления наёмниками."),
    version="3.0.0",
    lifespan=lifespan,
)


@app.get("/")
def root():
    return {
        "message": "MERCNET is running",
        "storage": "PostgreSQL via SQLAlchemy ORM",
    }


@app.post("/mercenaries", response_model=MercenaryRead, status_code=status.HTTP_201_CREATED)
def create_mercenary(data: MercenaryCreate, db: DbSession):
    mercenary = Mercenary(**data.model_dump())
    db.add(mercenary)
    db.commit()
    db.refresh(mercenary)
    return mercenary


@app.get("/mercenaries", response_model=list[MercenaryRead])
def get_mercenaries(db: DbSession):
    return db.scalars(select(Mercenary)).all()


@app.get("/mercenaries/{mercenary_id}", response_model=MercenaryRead)
def get_mercenary(mercenary_id: int, db: DbSession):
    mercenary = db.get(Mercenary, mercenary_id)
    if not mercenary:
        raise HTTPException(status_code=404, detail="Mercenary not found")
    return mercenary


@app.post("/contracts", response_model=ContractRead, status_code=status.HTTP_201_CREATED)
def create_contract(data: ContractCreate, db: DbSession):
    contract = Contract(**data.model_dump(), status=ContractStatus.OPEN)
    db.add(contract)
    db.commit()
    db.refresh(contract)
    return contract


@app.get("/contracts", response_model=list[ContractRead])
def get_contracts(
    db: DbSession,
    status: ContractStatus | None = None,
    district: str | None = None,
    min_reward: float | None = Query(default=None, ge=0),
    max_difficulty: int | None = Query(default=None, ge=1, le=10),
):
    statement = select(Contract)

    if status is not None:
        statement = statement.where(Contract.status == status)
    if district is not None:
        statement = statement.where(Contract.district == district)
    if min_reward is not None:
        statement = statement.where(Contract.reward >= min_reward)
    if max_difficulty is not None:
        statement = statement.where(Contract.difficulty <= max_difficulty)

    return db.scalars(statement).all()