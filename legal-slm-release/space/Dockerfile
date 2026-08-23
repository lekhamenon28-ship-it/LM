FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir torch==2.6.0 transformers==4.49.0 safetensors==0.5.3 fastapi==0.115.8 uvicorn==0.34.0
COPY app.py /app/app.py
ENV MODEL_ID=lekhamenon28/legal-slm-125m-2ep
EXPOSE 7860
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860"]

