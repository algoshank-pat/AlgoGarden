import argparse
import json
from knowledge import ingest


def main():
    parser = argparse.ArgumentParser(description='Index local restaurant policy Markdown with Ollama embeddings.')
    parser.add_argument('--business',required=True)
    args = parser.parse_args()
    print(json.dumps(ingest(args.business),indent=2))


if __name__ == '__main__':
    main()
