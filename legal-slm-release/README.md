# Legal SLM

Legal-domain small language model inference and evaluation application.

## Repository layout

- `vercel-app/` — Next.js evaluation interface
- `space/` — containerized Hugging Face Space inference API
- `modal_inference.py` — Modal GPU inference deployment
- `model/base/` — model card and lightweight model configuration
- `app-data/index.json` — compact evaluation/training run index

## Web application

```bash
cd vercel-app
npm install
cp .env.example .env.local
npm run dev
```

Large model weights, tokenizer artifacts, and raw training metrics are excluded
from Git because they are generated artifacts and should be published through a
dedicated model registry such as Hugging Face Hub.

