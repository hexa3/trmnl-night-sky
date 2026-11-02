FROM python:3.12-slim
# tzdata for zoneinfo; no other dependencies
RUN pip install --no-cache-dir tzdata
WORKDIR /app
COPY backend/ ./
ENV PORT=8080
EXPOSE 8080
USER nobody
CMD ["python3", "server.py"]
