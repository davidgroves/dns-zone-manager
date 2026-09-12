# DNS Zone Manager Development Container
FROM mcr.microsoft.com/devcontainers/python:3-3.13-bookworm

# Remove expired yarn repo before apt-get update
RUN rm -f /etc/apt/sources.list.d/yarn.list || true

# DNS tools for dig, nsupdate, rndc
RUN apt-get update && apt-get install -y --no-install-recommends \
    bind9-dnsutils \
    bind9-host \
    bind9utils \
    && rm -rf /var/lib/apt/lists/*

# Node.js 22
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Playwright browsers (Chromium, Firefox, WebKit) + OS dependencies.
# Install into a shared path so the vscode user finds them without a second
# download. iPad E2E projects use WebKit under the hood.
# Pin the installer to the lockfile version so the image matches package-lock.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
COPY package.json package-lock.json /tmp/playwright-install/
WORKDIR /tmp/playwright-install
RUN npm ci --ignore-scripts \
    && npx playwright install --with-deps \
    && chmod -R a+rwX /ms-playwright \
    && rm -rf /tmp/playwright-install /root/.npm
WORKDIR /

# Docker CLI (for viewing sibling container logs)
RUN curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

# uv package manager
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# DNS configuration files for easier CLI usage
# rndc.conf: allows 'rndc status' without -c flag
# dns-api-key.conf: allows 'nsupdate -k /etc/dns-api-key.conf' or 'dig -k /etc/dns-api-key.conf'
COPY .devcontainer/rndc.devcontainer.conf /etc/rndc.conf
COPY .devcontainer/dns-api-key.conf /etc/dns-api-key.conf
