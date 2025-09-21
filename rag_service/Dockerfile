FROM python:3.10-slim

# Tạo thư mục app
WORKDIR /app

# Cài đặt các dependencies cơ bản
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Cài streamlit cho UI
RUN pip install --no-cache-dir streamlit

# Copy toàn bộ code
COPY . .

# Default command (cho rag-service). 
# Container rag-ui sẽ override bằng docker-compose.yml
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]
