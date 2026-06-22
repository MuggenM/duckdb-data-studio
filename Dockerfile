FROM python:3.12-slim

# Install system build dependencies and openssl
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    openssl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy dependency specifications
COPY requirements.txt .

# Install python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code and initializer
COPY init_stack.py .
COPY main.py .
COPY local_file_picker/ local_file_picker/

# Create volume mount points
RUN mkdir -p /config /ducklake /databases

# Expose app port
EXPOSE 8085

# Run initializer and start web app
CMD ["bash", "-c", "python init_stack.py && python main.py"]
