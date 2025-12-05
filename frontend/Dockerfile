FROM node:20-alpine

WORKDIR /app

# Security: Run as non-root user (Commented out for Dev/Windows compatibility)
# RUN addgroup -g 1001 -S nodejs && \
#     adduser -S nextjs -u 1001

# Copy package files
# Copy package files
# Enable pnpm via corepack
RUN corepack enable && corepack prepare pnpm@latest --activate

# Copy package files
COPY package.json pnpm-lock.yaml ./

# Install dependencies with pnpm
RUN pnpm install --frozen-lockfile

# Copy application code
COPY . .

# Skip build for dev environment (we mount volume and run dev server)
# RUN pnpm run build

# Change ownership
# RUN chown -R nextjs:nodejs /app

# Switch to non-root user
# USER nextjs

EXPOSE 3000

ENV PORT 3000
ENV HOSTNAME "0.0.0.0"

CMD ["pnpm", "dev"]
