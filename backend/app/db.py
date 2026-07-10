from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    inspect,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"

    edinet_code: Mapped[str] = mapped_column(String(6), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    name_en: Mapped[Optional[str]] = mapped_column(String(255))
    sec_code: Mapped[Optional[str]] = mapped_column(String(10), index=True)
    jcn: Mapped[Optional[str]] = mapped_column(String(13))
    listing_status: Mapped[Optional[str]] = mapped_column(String(20), index=True)
    industry: Mapped[Optional[str]] = mapped_column(String(50), index=True)
    submitter_type: Mapped[Optional[str]] = mapped_column(String(80))
    location: Mapped[Optional[str]] = mapped_column(String(255))
    fiscal_year_end: Mapped[Optional[str]] = mapped_column(String(20))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    financials: Mapped[list["Financial"]] = relationship(back_populates="company")
    filings: Mapped[list["Filing"]] = relationship(back_populates="company")
    stock_quote: Mapped[Optional["StockQuote"]] = relationship(
        back_populates="company", uselist=False
    )
    real_estate_properties: Mapped[list["RealEstateProperty"]] = relationship(
        back_populates="company"
    )
    quarterly_financials: Mapped[list["QuarterlyFinancial"]] = relationship(
        back_populates="company"
    )
    profile: Mapped[Optional["CompanyProfile"]] = relationship(
        back_populates="company", uselist=False
    )


class CompanyProfile(Base):
    __tablename__ = "company_profiles"

    edinet_code: Mapped[str] = mapped_column(
        ForeignKey("companies.edinet_code"), primary_key=True
    )
    doc_id: Mapped[Optional[str]] = mapped_column(ForeignKey("filings.doc_id"), index=True)
    fiscal_year_end: Mapped[Optional[str]] = mapped_column(String(10))
    business_description: Mapped[Optional[str]] = mapped_column(Text)
    company_history: Mapped[Optional[str]] = mapped_column(Text)
    employees_text: Mapped[Optional[str]] = mapped_column(Text)
    affiliated_entities: Mapped[Optional[str]] = mapped_column(Text)
    employee_count: Mapped[Optional[int]] = mapped_column(Integer)
    capital_stock_m: Mapped[Optional[float]] = mapped_column(Float)
    issued_shares: Mapped[Optional[str]] = mapped_column(String(40))
    parse_status: Mapped[str] = mapped_column(String(20), default="empty")
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    company: Mapped["Company"] = relationship(back_populates="profile")


class Filing(Base):
    __tablename__ = "filings"

    doc_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    edinet_code: Mapped[str] = mapped_column(ForeignKey("companies.edinet_code"), index=True)
    doc_type_code: Mapped[str] = mapped_column(String(8), index=True)
    doc_description: Mapped[Optional[str]] = mapped_column(Text)
    period_start: Mapped[Optional[str]] = mapped_column(String(10))
    period_end: Mapped[Optional[str]] = mapped_column(String(10))
    submit_date_time: Mapped[Optional[str]] = mapped_column(String(20))
    file_date: Mapped[str] = mapped_column(String(10), index=True)
    has_xbrl: Mapped[bool] = mapped_column(Boolean, default=False)
    has_pdf: Mapped[bool] = mapped_column(Boolean, default=False)
    has_csv: Mapped[bool] = mapped_column(Boolean, default=False)

    company: Mapped["Company"] = relationship(back_populates="filings")
    financial: Mapped[Optional["Financial"]] = relationship(back_populates="filing", uselist=False)


class Financial(Base):
    __tablename__ = "financials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    edinet_code: Mapped[str] = mapped_column(ForeignKey("companies.edinet_code"), index=True)
    doc_id: Mapped[str] = mapped_column(ForeignKey("filings.doc_id"), unique=True)
    fiscal_year_end: Mapped[Optional[str]] = mapped_column(String(10), index=True)
    revenue: Mapped[Optional[float]] = mapped_column(Float)
    operating_income: Mapped[Optional[float]] = mapped_column(Float)
    ordinary_income: Mapped[Optional[float]] = mapped_column(Float)
    net_income: Mapped[Optional[float]] = mapped_column(Float)
    total_assets: Mapped[Optional[float]] = mapped_column(Float)
    net_assets: Mapped[Optional[float]] = mapped_column(Float)
    eps: Mapped[Optional[float]] = mapped_column(Float)
    operating_margin: Mapped[Optional[float]] = mapped_column(Float)
    roe: Mapped[Optional[float]] = mapped_column(Float)
    revenue_growth: Mapped[Optional[float]] = mapped_column(Float)
    operating_cf: Mapped[Optional[float]] = mapped_column(Float)
    investing_cf: Mapped[Optional[float]] = mapped_column(Float)
    financing_cf: Mapped[Optional[float]] = mapped_column(Float)
    cash_and_deposits: Mapped[Optional[float]] = mapped_column(Float)
    interest_bearing_debt: Mapped[Optional[float]] = mapped_column(Float)
    total_liabilities: Mapped[Optional[float]] = mapped_column(Float)
    bps: Mapped[Optional[float]] = mapped_column(Float)
    dividend_per_share: Mapped[Optional[float]] = mapped_column(Float)
    roa: Mapped[Optional[float]] = mapped_column(Float)
    equity_ratio: Mapped[Optional[float]] = mapped_column(Float)
    debt_equity_ratio: Mapped[Optional[float]] = mapped_column(Float)
    parse_version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    company: Mapped["Company"] = relationship(back_populates="financials")
    filing: Mapped["Filing"] = relationship(back_populates="financial")


class StockQuote(Base):
    __tablename__ = "stock_quotes"

    edinet_code: Mapped[str] = mapped_column(
        ForeignKey("companies.edinet_code"), primary_key=True
    )
    ticker: Mapped[Optional[str]] = mapped_column(String(20))
    price: Mapped[Optional[float]] = mapped_column(Float)
    market_cap: Mapped[Optional[float]] = mapped_column(Float)
    per: Mapped[Optional[float]] = mapped_column(Float)
    pbr: Mapped[Optional[float]] = mapped_column(Float)
    per_edinet: Mapped[Optional[float]] = mapped_column(Float)
    pbr_edinet: Mapped[Optional[float]] = mapped_column(Float)
    dividend_yield: Mapped[Optional[float]] = mapped_column(Float)
    fifty_two_week_high: Mapped[Optional[float]] = mapped_column(Float)
    fifty_two_week_low: Mapped[Optional[float]] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    company: Mapped["Company"] = relationship(back_populates="stock_quote")


class RealEstateProperty(Base):
    __tablename__ = "real_estate_properties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    edinet_code: Mapped[str] = mapped_column(ForeignKey("companies.edinet_code"), index=True)
    doc_id: Mapped[str] = mapped_column(ForeignKey("filings.doc_id"), index=True)
    fiscal_year_end: Mapped[Optional[str]] = mapped_column(String(10), index=True)
    facility_name: Mapped[str] = mapped_column(String(255))
    location: Mapped[str] = mapped_column(String(255))
    prefecture: Mapped[Optional[str]] = mapped_column(String(20), index=True)
    section: Mapped[str] = mapped_column(String(40), default="major_facilities")
    category: Mapped[Optional[str]] = mapped_column(String(80))
    building_scale: Mapped[Optional[str]] = mapped_column(String(120))
    building_area_sqm: Mapped[Optional[float]] = mapped_column(Float)
    building_book_value: Mapped[Optional[float]] = mapped_column(Float)
    land_area_sqm: Mapped[Optional[float]] = mapped_column(Float)
    land_book_value: Mapped[Optional[float]] = mapped_column(Float)
    other_book_value: Mapped[Optional[float]] = mapped_column(Float)
    total_book_value: Mapped[Optional[float]] = mapped_column(Float)
    machinery_book_value: Mapped[Optional[float]] = mapped_column(Float)
    lease_book_value: Mapped[Optional[float]] = mapped_column(Float)
    completion_year: Mapped[Optional[str]] = mapped_column(String(4))
    employees: Mapped[Optional[int]] = mapped_column(Integer)
    footnotes: Mapped[Optional[str]] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    company: Mapped["Company"] = relationship(back_populates="real_estate_properties")


class RealEstateSync(Base):
    __tablename__ = "real_estate_sync"

    doc_id: Mapped[str] = mapped_column(ForeignKey("filings.doc_id"), primary_key=True)
    edinet_code: Mapped[str] = mapped_column(ForeignKey("companies.edinet_code"), index=True)
    fiscal_year_end: Mapped[Optional[str]] = mapped_column(String(10))
    property_count: Mapped[int] = mapped_column(Integer, default=0)
    parse_status: Mapped[str] = mapped_column(String(20), default="empty")
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class QuarterlyFinancial(Base):
    __tablename__ = "quarterly_financials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    edinet_code: Mapped[str] = mapped_column(ForeignKey("companies.edinet_code"), index=True)
    doc_id: Mapped[str] = mapped_column(ForeignKey("filings.doc_id"), unique=True)
    period_start: Mapped[Optional[str]] = mapped_column(String(10))
    period_end: Mapped[Optional[str]] = mapped_column(String(10), index=True)
    quarter_number: Mapped[Optional[int]] = mapped_column(Integer)
    revenue_cumulative: Mapped[Optional[float]] = mapped_column(Float)
    revenue_prior_year_cum: Mapped[Optional[float]] = mapped_column(Float)
    revenue_single: Mapped[Optional[float]] = mapped_column(Float)
    operating_income_cumulative: Mapped[Optional[float]] = mapped_column(Float)
    operating_income_prior_year_cum: Mapped[Optional[float]] = mapped_column(Float)
    operating_income_single: Mapped[Optional[float]] = mapped_column(Float)
    net_income_cumulative: Mapped[Optional[float]] = mapped_column(Float)
    net_income_prior_year_cum: Mapped[Optional[float]] = mapped_column(Float)
    net_income_single: Mapped[Optional[float]] = mapped_column(Float)
    eps_cumulative: Mapped[Optional[float]] = mapped_column(Float)
    eps_prior_year_cum: Mapped[Optional[float]] = mapped_column(Float)
    revenue_yoy: Mapped[Optional[float]] = mapped_column(Float)
    operating_income_yoy: Mapped[Optional[float]] = mapped_column(Float)
    net_income_yoy: Mapped[Optional[float]] = mapped_column(Float)
    eps_yoy: Mapped[Optional[float]] = mapped_column(Float)
    revenue_qoq: Mapped[Optional[float]] = mapped_column(Float)
    operating_income_qoq: Mapped[Optional[float]] = mapped_column(Float)
    net_income_qoq: Mapped[Optional[float]] = mapped_column(Float)
    parse_status: Mapped[str] = mapped_column(String(20), default="ok")
    parse_version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    company: Mapped["Company"] = relationship(back_populates="quarterly_financials")


class CompanyNewsArticle(Base):
    __tablename__ = "company_news_articles"
    __table_args__ = (UniqueConstraint("edinet_code", "link", name="uq_news_edinet_link"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    edinet_code: Mapped[str] = mapped_column(ForeignKey("companies.edinet_code"), index=True)
    link: Mapped[str] = mapped_column(String(2048))
    title: Mapped[str] = mapped_column(String(512))
    published_at: Mapped[Optional[str]] = mapped_column(String(30), index=True)
    source_name: Mapped[Optional[str]] = mapped_column(String(255))
    summary: Mapped[Optional[str]] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class CompanyTrendPoint(Base):
    __tablename__ = "company_trend_points"
    __table_args__ = (UniqueConstraint("edinet_code", "point_date", name="uq_trend_edinet_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    edinet_code: Mapped[str] = mapped_column(ForeignKey("companies.edinet_code"), index=True)
    point_date: Mapped[str] = mapped_column(String(10), index=True)
    value: Mapped[int] = mapped_column(Integer)
    keyword: Mapped[Optional[str]] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ExternalMediaSync(Base):
    __tablename__ = "external_media_sync"

    edinet_code: Mapped[str] = mapped_column(ForeignKey("companies.edinet_code"), primary_key=True)
    news_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    trend_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    news_error: Mapped[Optional[str]] = mapped_column(String(40))
    trend_error: Mapped[Optional[str]] = mapped_column(String(40))
    news_total: Mapped[int] = mapped_column(Integer, default=0)
    trend_total: Mapped[int] = mapped_column(Integer, default=0)


class ExternalMediaBatchState(Base):
    __tablename__ = "external_media_batch_state"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ShortSellingBalance(Base):
    """JPX が公表する「空売り残高に関する情報」（残高割合0.5%以上の個別ポジション）。

    無料 API では配布されず、公表ページからは日々消えていくため、
    日次で自前収集して時系列を積み上げること自体が優位性になる。
    """

    __tablename__ = "short_selling_balances"
    __table_args__ = (
        UniqueConstraint(
            "sec_code", "holder_name", "calc_date", name="uq_short_code_holder_date"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sec_code: Mapped[str] = mapped_column(String(10), index=True)
    edinet_code: Mapped[Optional[str]] = mapped_column(
        ForeignKey("companies.edinet_code"), index=True
    )
    company_name: Mapped[Optional[str]] = mapped_column(String(255))
    holder_name: Mapped[str] = mapped_column(String(255))
    short_ratio: Mapped[Optional[float]] = mapped_column(Float)
    short_shares: Mapped[Optional[float]] = mapped_column(Float)
    prev_ratio: Mapped[Optional[float]] = mapped_column(Float)
    prev_calc_date: Mapped[Optional[str]] = mapped_column(String(10))
    calc_date: Mapped[str] = mapped_column(String(10), index=True)
    published_date: Mapped[Optional[str]] = mapped_column(String(10), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False, "timeout": 60}
    if settings.database_url.startswith("sqlite")
    else {},
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    if settings.database_url.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    migrate_db()


FINANCIAL_COLUMN_MIGRATIONS: list[tuple[str, str]] = [
    ("operating_cf", "FLOAT"),
    ("investing_cf", "FLOAT"),
    ("financing_cf", "FLOAT"),
    ("cash_and_deposits", "FLOAT"),
    ("interest_bearing_debt", "FLOAT"),
    ("total_liabilities", "FLOAT"),
    ("bps", "FLOAT"),
    ("dividend_per_share", "FLOAT"),
    ("roa", "FLOAT"),
    ("equity_ratio", "FLOAT"),
    ("debt_equity_ratio", "FLOAT"),
    ("parse_version", "INTEGER DEFAULT 1"),
]


def migrate_db() -> None:
    if not settings.database_url.startswith("sqlite"):
        return
    inspector = inspect(engine)
    if "financials" not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns("financials")}
    with engine.begin() as conn:
        for name, col_type in FINANCIAL_COLUMN_MIGRATIONS:
            if name not in existing:
                conn.execute(text(f"ALTER TABLE financials ADD COLUMN {name} {col_type}"))
        if "parse_version" in existing or any(n == "parse_version" for n, _ in FINANCIAL_COLUMN_MIGRATIONS):
            conn.execute(text("UPDATE financials SET parse_version = 1 WHERE parse_version IS NULL"))
        if "quarterly_financials" in inspector.get_table_names():
            qcols = {col["name"] for col in inspector.get_columns("quarterly_financials")}
            if "parse_status" not in qcols:
                conn.execute(text("ALTER TABLE quarterly_financials ADD COLUMN parse_status VARCHAR(20) DEFAULT 'ok'"))
                conn.execute(text("UPDATE quarterly_financials SET parse_status = 'ok' WHERE parse_status IS NULL"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
