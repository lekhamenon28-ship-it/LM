from __future__ import annotations

import os
import time

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer


MODEL_ID = os.environ.get("MODEL_ID", "lekhamenon28/legal-slm-125m-2ep")
tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
model = AutoModelForCausalLM.from_pretrained(MODEL_ID, torch_dtype=torch.float32)
model.eval()

app = FastAPI(title="Legal SLM Completion API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class CompletionRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4_000)
    max_new_tokens: int = Field(default=120, ge=1, le=256)
    temperature: float = Field(default=0.8, ge=0.1, le=2.0)
    top_p: float = Field(default=0.92, ge=0.1, le=1.0)


@app.get("/")
def root():
    return {
        "model": MODEL_ID,
        "kind": "base text completer",
        "warning": "Not legal or financial advice; outputs may hallucinate.",
        "endpoint": "POST /generate",
    }


@app.get("/health")
def health():
    return {"ok": True, "model": MODEL_ID}


@app.post("/generate")
def generate(request: CompletionRequest):
    started = time.monotonic()
    encoded = tokenizer(request.prompt, return_tensors="pt", truncation=True, max_length=768)
    try:
        with torch.inference_mode():
            output = model.generate(
                **encoded,
                max_new_tokens=request.max_new_tokens,
                do_sample=True,
                temperature=request.temperature,
                top_p=request.top_p,
                repetition_penalty=1.08,
                pad_token_id=tokenizer.eos_token_id,
            )
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"generation failed: {type(error).__name__}") from error
    full_text = tokenizer.decode(output[0], skip_special_tokens=True)
    continuation = full_text[len(request.prompt):] if full_text.startswith(request.prompt) else full_text
    return {
        "completion": continuation,
        "full_text": full_text,
        "model": MODEL_ID,
        "latency_seconds": round(time.monotonic() - started, 3),
        "warning": "Base-model completion only. Verify every legal or financial claim.",
    }
