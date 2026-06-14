from sqlalchemy import create_engine, Column, String, Integer, Float, Text, DateTime, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from datetime import datetime
import os
import uuid
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("DB_PATH", "./irrigation.db")
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class KnowledgeNode(Base):
    __tablename__ = "knowledge_nodes"
    id = Column(Integer, primary_key=True, autoincrement=True)
    level = Column(Integer, default=0)
    content = Column(Text)
    metadata_info = Column(Text, default="{}")
    parent_id = Column(Integer, nullable=True)
    cluster_id = Column(Integer, default=0)
    tag = Column(String(50), default="domain_knowledge")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class CropParameter(Base):
    __tablename__ = "crop_parameters"
    id = Column(Integer, primary_key=True, autoincrement=True)
    crop_name = Column(String(50), unique=True)
    stage_1_kc = Column(Float, default=0.3)
    stage_2_kc = Column(Float, default=0.5)
    stage_3_kc = Column(Float, default=1.15)
    stage_4_kc = Column(Float, default=0.4)
    root_depth = Column(Float, default=0.8)
    stage_1_days = Column(Integer, default=30)
    stage_2_days = Column(Integer, default=45)
    stage_3_days = Column(Integer, default=60)
    stage_4_days = Column(Integer, default=30)


class SoilParameter(Base):
    __tablename__ = "soil_parameters"
    id = Column(Integer, primary_key=True, autoincrement=True)
    soil_type = Column(String(50), unique=True)
    field_capacity = Column(Float, default=0.32)
    wilting_point = Column(Float, default=0.12)
    available_water = Column(Float, default=0.20)


class ConversationHistory(Base):
    __tablename__ = "conversation_history"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(100), index=True)
    role = Column(String(20))
    content = Column(Text)
    timestamp = Column(DateTime, default=datetime.now)
    context = Column(JSON, default={})


class GeneratedPlan(Base):
    __tablename__ = "generated_plans"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(100), index=True)
    plan_content = Column(Text)
    explanation = Column(Text)
    quality_report = Column(JSON)
    created_at = Column(DateTime, default=datetime.now)


class ProcessedDocument(Base):
    __tablename__ = "processed_documents"
    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String(500), unique=True)
    md5_hash = Column(String(32))
    doc_source = Column(String(500))
    processed_at = Column(DateTime, default=datetime.now)


class SystemConfig(Base):
    __tablename__ = "system_config"
    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(100), unique=True)
    value = Column(Text)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


def init_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        crops = [
            CropParameter(crop_name="小麦", stage_1_kc=0.3, stage_2_kc=0.5, stage_3_kc=1.15, stage_4_kc=0.4, root_depth=0.8, stage_1_days=30, stage_2_days=45, stage_3_days=60, stage_4_days=30),
            CropParameter(crop_name="水稻", stage_1_kc=1.1, stage_2_kc=1.2, stage_3_kc=1.15, stage_4_kc=0.9, root_depth=0.6, stage_1_days=25, stage_2_days=50, stage_3_days=45, stage_4_days=20),
            CropParameter(crop_name="玉米", stage_1_kc=0.3, stage_2_kc=0.5, stage_3_kc=1.1, stage_4_kc=0.5, root_depth=1.0, stage_1_days=25, stage_2_days=35, stage_3_days=45, stage_4_days=20),
            CropParameter(crop_name="棉花", stage_1_kc=0.35, stage_2_kc=0.6, stage_3_kc=1.15, stage_4_kc=0.7, root_depth=1.2, stage_1_days=30, stage_2_days=50, stage_3_days=60, stage_4_days=55),
            CropParameter(crop_name="蔬菜", stage_1_kc=0.7, stage_2_kc=0.8, stage_3_kc=1.05, stage_4_kc=0.9, root_depth=0.4, stage_1_days=20, stage_2_days=30, stage_3_days=30, stage_4_days=10),
        ]
        for c in crops:
            if not db.query(CropParameter).filter(CropParameter.crop_name == c.crop_name).first():
                db.add(c)

        soils = [
            SoilParameter(soil_type="壤土", field_capacity=0.32, wilting_point=0.12, available_water=0.20),
            SoilParameter(soil_type="砂土", field_capacity=0.18, wilting_point=0.06, available_water=0.12),
            SoilParameter(soil_type="粘土", field_capacity=0.42, wilting_point=0.18, available_water=0.24),
            SoilParameter(soil_type="粉壤土", field_capacity=0.28, wilting_point=0.10, available_water=0.18),
        ]
        for s in soils:
            if not db.query(SoilParameter).filter(SoilParameter.soil_type == s.soil_type).first():
                db.add(s)

        db.commit()
        logger.info("[DB] 数据库初始化完成")
    finally:
        db.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()