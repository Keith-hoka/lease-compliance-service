from fastapi import FastAPI

app = FastAPI(title="Lease Compliance Service")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
