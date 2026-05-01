#!/usr/bin/env python3
"""
ImageOpz Backend Service - Main entry point
Starts the FastAPI server for image optimization
"""

import sys
import argparse
import uvicorn
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from app.config import settings
from app.main import app


def main():
    parser = argparse.ArgumentParser(
        description="ImageOpz Backend Service",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                    # Start server (debug mode if configured)
  %(prog)s --host 0.0.0.0     # Start on all interfaces
  %(prog)s --port 8000        # Use custom port
  %(prog)s --workers 4        # Use 4 worker processes
        """
    )
    
    parser.add_argument(
        "--host",
        default=settings.api_host,
        help=f"Host to bind to (default: {settings.api_host})"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=settings.api_port,
        help=f"Port to bind to (default: {settings.api_port})"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1 if settings.debug else settings.api_workers,
        help=f"Number of worker processes (default: {settings.api_workers})"
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        default=settings.debug,
        help="Enable auto-reload on code changes"
    )
    parser.add_argument(
        "--env",
        default=settings.environment,
        choices=["development", "staging", "production"],
        help="Environment mode"
    )
    
    args = parser.parse_args()
    
    print(f"""
╔══════════════════════════════════════════╗
║     ImageOpz Backend Service v1.0.0      ║
╚══════════════════════════════════════════╝

Configuration:
  Host:           {args.host}
  Port:           {args.port}
  Workers:        {args.workers}
  Environment:    {args.env}
  Auto-reload:    {args.reload}
  Debug:          {settings.debug}

Starting server...
""")
    
    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        workers=args.workers,
        reload=args.reload,
        log_level="debug" if settings.debug else "info",
        access_log=True
    )


if __name__ == "__main__":
    main()
