variable "IMAGE_REPO" {
  default = "nanaon-private-server"
}

variable "IMAGE_TAG" {
  default = "latest"
}

variable "PLATFORMS" {
  default = "linux/amd64"
}

group "default" {
  targets = ["server"]
}


target "base" {
  context    = "."
  dockerfile = "docker/Dockerfile"
  platforms  = split(",", PLATFORMS)

  labels = {
    "org.opencontainers.image.title"       = "Nanaon Private Server"
    "org.opencontainers.image.description" = "22/7 音楽の時間 private server: Flask API, asset CDN and TLS gateway"
    "org.opencontainers.image.source"      = "https://github.com/UnspokenSpell/Nanaon-Private-Server"
    "org.opencontainers.image.licenses"    = "MIT"
    "org.opencontainers.image.version"     = IMAGE_TAG
  }

  # only activate when running under GitHub Actions (ignored elsewhere).
  cache-from = ["type=gha,scope=server"]
  cache-to   = ["type=gha,scope=server,mode=max"]
}

# Local development
target "server" {
  inherits = ["base"]
  tags     = ["${IMAGE_REPO}:${IMAGE_TAG}"]
  output   = ["type=docker"]
}

# Publish: push the image to the configured registry
target "publish" {
  inherits = ["base"]
  tags     = ["${IMAGE_REPO}:${IMAGE_TAG}"]
  output   = ["type=registry"]
}
