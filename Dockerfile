FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/app/data HOST=0.0.0.0 PORT=8765
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home appuser
COPY app ./app
COPY third_party ./third_party
COPY run.py .
RUN mkdir -p /app/data && chown -R appuser:appuser /app
USER appuser
EXPOSE 8765
CMD ["python", "run.py"]
