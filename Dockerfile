FROM python:3.13-slim
WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy Python/backend files first, then the frontend explicitly so a missing
# static directory fails the image build instead of becoming a runtime 500.
COPY *.py /app/
COPY static/ /app/static/
COPY README*.md /app/

RUN test -f /app/static/index.html

CMD ["sh", "-c", "uvicorn dashboard_api:app --host 0.0.0.0 --port ${PORT:-8000}"]
