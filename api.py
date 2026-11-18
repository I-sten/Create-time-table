"""HTTP API for the timetable solver.

Run with: uvicorn api:app --host 0.0.0.0 --port 8000
Interactive documentation: http://localhost:8000/docs
"""

from __future__ import annotations

import copy
import csv
import io
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from timetable_solver import build_lessons, generate_timetable, validate_data


class Period(BaseModel):
    model_config = ConfigDict(extra="allow")
    number: int | str
    name: str | None = None
    start: str | None = None
    end: str | None = None


class Break(BaseModel):
    model_config = ConfigDict(extra="allow")
    after_period: int | str
    name: str = "Break"


class School(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    days: list[str | dict[str, Any]]
    periods: list[int | str | Period]
    breaks: list[Break] = Field(default_factory=list)


class Teacher(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: str | None = None


class SubjectAssignment(BaseModel):
    model_config = ConfigDict(extra="allow")
    subject: str
    teacher: str
    sessions_per_week: int = Field(ge=1)
    max_sessions_per_day: int | None = Field(default=None, ge=1)


class SchoolClass(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: str
    subjects: list[SubjectAssignment]


class Rules(BaseModel):
    model_config = ConfigDict(extra="allow")
    teacher_unavailable: list[dict[str, Any]] = Field(default_factory=list)
    class_unavailable: list[dict[str, Any]] = Field(default_factory=list)
    fixed_lessons: list[dict[str, Any]] = Field(default_factory=list)
    free_period_restrictions: list[dict[str, Any]] = Field(default_factory=list)
    subject_position_preferences: list[dict[str, Any]] = Field(default_factory=list)
    max_class_consecutive_periods: int | None = Field(default=None, ge=1)
    max_teacher_consecutive_periods: int | None = Field(default=None, ge=1)
    spread_subject_across_days: bool = True


class TimetableConfiguration(BaseModel):
    model_config = ConfigDict(extra="allow")
    school: School
    teachers: list[Teacher]
    classes: list[SchoolClass]
    rules: Rules = Field(default_factory=Rules)


class ValidationResponse(BaseModel):
    valid: bool
    total_lessons: int
    message: str


CSV_FIELDS = ["class", "day", "period", "period_name", "start", "end", "subject", "teacher", "session"]

app = FastAPI(
    title="Automatic Timetable Solver API",
    version="1.0.0",
    description="Validate school timetable settings and generate independently verified timetables.",
)


def configuration_dict(configuration: TimetableConfiguration) -> dict[str, Any]:
    # The solver normalizes its input in place, so every request gets a private copy.
    return copy.deepcopy(configuration.model_dump(exclude_none=True))


def solve(configuration: TimetableConfiguration) -> dict[str, Any]:
    data = configuration_dict(configuration)
    validate_data(data)
    result = generate_timetable(data)
    if result is None:
        raise HTTPException(status_code=409, detail="The supplied hard constraints are infeasible.")
    return result


@app.get("/", tags=["service"])
def service_info() -> dict[str, str]:
    return {
        "service": "Automatic Timetable Solver API",
        "version": "1.0.0",
        "health": "/api/v1/health",
        "documentation": "/docs",
    }


@app.get("/api/v1/health", tags=["service"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "timetable-solver", "version": "1.0.0"}


@app.post("/api/v1/timetables/validate", response_model=ValidationResponse, tags=["timetables"])
async def validate_configuration(configuration: TimetableConfiguration) -> ValidationResponse:
    data = configuration_dict(configuration)
    try:
        await run_in_threadpool(validate_data, data)
        total = len(build_lessons(data))
        return ValidationResponse(valid=True, total_lessons=total, message="Configuration is structurally valid.")
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/timetables/generate", tags=["timetables"])
async def generate(configuration: TimetableConfiguration) -> dict[str, Any]:
    try:
        return await run_in_threadpool(solve, configuration)
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Timetable generation failed: {exc}") from exc


@app.post("/api/v1/timetables/generate.csv", tags=["timetables"])
async def generate_csv(
    configuration: TimetableConfiguration,
    filename: str = Query(default="timetable.csv", pattern=r"^[A-Za-z0-9_.-]+$"),
) -> StreamingResponse:
    try:
        result = await run_in_threadpool(solve, configuration)
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Timetable generation failed: {exc}") from exc

    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for row in result["timetable"]:
        writer.writerow({key: row.get(key) for key in CSV_FIELDS})
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

