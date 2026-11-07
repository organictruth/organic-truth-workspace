FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mail_ack ./mail_ack

USER 65532:65532
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "mail_ack", "serve"]
