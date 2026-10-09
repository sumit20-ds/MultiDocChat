# Use official Python image
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set workdir
WORKDIR /app

#Install build-essential, poppler-utils, and curl, then clean up apt cache
RUN apt-get update && apt-get install -y build-essential poppler-utils curl && rm -rf /var/lib/apt/lists/* 

# Install uv (Python package/dependency manager) download and run the uv installation script from the official website, which installs uv in the user's local bin directory.
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"
ENV UV_LINK_MODE=copy
ENV PYTHONPATH="/app:/app/multi_doc_chat"

# Copy dependency manifests for better layer caching
COPY requirements.txt ./

# Install dependencies into the system interpreter using uv pip
RUN uv pip install --system -r requirements.txt

# Copy project files
COPY . .


#expose port 8080 for the FastAPI application to listen on
EXPOSE 8080 

# Run FastAPI with uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--reload"]

# Replace last CMD in prod
#CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "4"]


#---to run the docker container, use the following commands in the terminal---
#docker rm -f multidocchat
#docker run -d -p 8080:8080 --env-file .env --name multidocchat multidocchat