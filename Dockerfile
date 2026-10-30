FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./
COPY static ./static

ENV PORT=8765
ENV HOST=0.0.0.0
EXPOSE 8765
VOLUME ["/app/data"]

CMD ["python", "app.py"]
