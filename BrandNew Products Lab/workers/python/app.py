"""Optional batch worker. The Next.js app does not need this process to boot."""

from fastapi import FastAPI
from pydantic import BaseModel, Field

from trendmath import read_trend

app = FastAPI(title="BrandNew Products Lab worker", version="0.1.0")


class SeriesIn(BaseModel):
    series: list[float] = Field(min_length=1)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/analyze/trend")
def analyze_trend(body: SeriesIn) -> dict[str, float | str]:
    return read_trend(body.series)
