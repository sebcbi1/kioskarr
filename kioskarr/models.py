import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from kioskarr.db import Base


class PublicationType(str, enum.Enum):
    magazine = "magazine"
    newspaper = "newspaper"


class FormatPreference(str, enum.Enum):
    pdf = "pdf"
    cbr = "cbr"
    any = "any"


class GrabStatus(str, enum.Enum):
    downloading = "downloading"
    completed = "completed"
    imported = "imported"
    needs_review = "needs_review"
    failed = "failed"


class Publication(Base):
    __tablename__ = "publications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[PublicationType] = mapped_column(
        Enum(PublicationType), default=PublicationType.magazine, nullable=False
    )
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    format_preference: Mapped[FormatPreference] = mapped_column(
        Enum(FormatPreference), default=FormatPreference.any, nullable=False
    )
    min_seeders: Mapped[int] = mapped_column(Integer, default=1)
    target_dir: Mapped[str] = mapped_column(String, nullable=False)
    monitored: Mapped[bool] = mapped_column(default=True)

    # How many issues to grab on the very first search (cold start), instead of
    # every historical issue the indexer happens to return.
    grab_last_n: Mapped[int] = mapped_column(Integer, default=1)
    # Permanent floor set once cold start resolves: nothing at or below this
    # identifier is ever grabbed again, even if it resurfaces in later searches.
    # None means "not yet initialized" — the next search cycle will cold-start.
    baseline_identifier: Mapped[str | None] = mapped_column(String, nullable=True)

    issues: Mapped[list["Issue"]] = relationship(back_populates="publication")
    grabs: Mapped[list["Grab"]] = relationship(back_populates="publication")

    def all_search_terms(self) -> list[str]:
        return [self.title, *self.aliases]


class Issue(Base):
    __tablename__ = "issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    publication_id: Mapped[int] = mapped_column(ForeignKey("publications.id"), nullable=False)
    identifier: Mapped[str] = mapped_column(String, nullable=False)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    source_release_title: Mapped[str] = mapped_column(String, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    publication: Mapped[Publication] = relationship(back_populates="issues")


class Grab(Base):
    __tablename__ = "grabs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    publication_id: Mapped[int] = mapped_column(ForeignKey("publications.id"), nullable=False)
    release_title: Mapped[str] = mapped_column(String, nullable=False)
    release_guid: Mapped[str] = mapped_column(String, nullable=False)
    identifier: Mapped[str] = mapped_column(String, nullable=False)
    torrent_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    indexer_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[GrabStatus] = mapped_column(
        Enum(GrabStatus), default=GrabStatus.downloading, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    publication: Mapped[Publication] = relationship(back_populates="grabs")


class ReviewItem(Base):
    __tablename__ = "review_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    grab_id: Mapped[int] = mapped_column(ForeignKey("grabs.id"), nullable=False)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    candidate_publication_id: Mapped[int | None] = mapped_column(
        ForeignKey("publications.id"), nullable=True
    )
    resolved: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    grab: Mapped[Grab] = relationship()
