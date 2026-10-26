"""Local server entry point with structured application logs."""

import argparse
import logging

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the endpoint gateway")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    uvicorn.run("inference_gateway.app:create_app", factory=True, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
