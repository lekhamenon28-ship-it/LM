"""Scale-to-zero Modal inference API for the Hugging Face legal SLM."""

from __future__ import annotations

import modal


MODEL_ID = "lekhamenon28/legal-slm-125m-2ep"
MODEL_DIR = "/models/legal-slm-125m-2ep"


def download_model() -> None:
    from huggingface_hub import snapshot_download

    snapshot_download(MODEL_ID, local_dir=MODEL_DIR)


image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "torch==2.6.0",
        "transformers==4.49.0",
        "safetensors==0.5.3",
        "huggingface-hub==0.29.3",
        "fastapi[standard]==0.115.8",
    )
    .run_function(download_model)
)

app = modal.App("legal-slm-125m-inference-v2")


@app.cls(
    image=image,
    cpu=4,
    memory=4096,
    scaledown_window=120,
    timeout=10 * 60,
)
class LegalSLM:
    @modal.enter()
    def load(self):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        torch.set_num_threads(4)
        self.tokenizer = AutoTokenizer.from_pretrained(MODEL_DIR, local_files_only=True)
        self.model = AutoModelForCausalLM.from_pretrained(
            MODEL_DIR,
            local_files_only=True,
            torch_dtype=torch.float32,
        )
        self.model.eval()

    @modal.fastapi_endpoint(method="POST", docs=True)
    def generate(self, request: dict) -> dict:
        import time
        import torch
        from fastapi import HTTPException

        prompt = str(request.get("prompt", "")).strip()
        if not prompt:
            raise HTTPException(status_code=400, detail="prompt is required")
        if len(prompt) > 4_000:
            raise HTTPException(status_code=400, detail="prompt exceeds 4,000 characters")
        max_new_tokens = max(1, min(int(request.get("max_new_tokens", 120)), 256))
        temperature = max(0.1, min(float(request.get("temperature", 0.8)), 2.0))
        top_p = max(0.1, min(float(request.get("top_p", 0.92)), 1.0))
        started = time.monotonic()
        encoded = self.tokenizer(prompt, return_tensors="pt", truncation=True, max_length=768)
        input_ids = encoded["input_ids"]
        attention_mask = encoded.get("attention_mask")
        with torch.inference_mode():
            output = self.model.generate(
                input_ids=input_ids,
                attention_mask=attention_mask,
                max_new_tokens=max_new_tokens,
                do_sample=True,
                temperature=temperature,
                top_p=top_p,
                repetition_penalty=1.08,
                pad_token_id=self.tokenizer.eos_token_id,
            )
        full_text = self.tokenizer.decode(output[0], skip_special_tokens=True)
        continuation = full_text[len(prompt):] if full_text.startswith(prompt) else full_text
        return {
            "completion": continuation,
            "full_text": full_text,
            "model": MODEL_ID,
            "latency_seconds": round(time.monotonic() - started, 3),
            "warning": "Base-model completion only. Verify every legal or financial claim.",
        }
