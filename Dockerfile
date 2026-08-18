FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 创建数据目录
RUN mkdir -p /data

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
