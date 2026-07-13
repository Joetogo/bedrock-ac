import os
os.environ.setdefault("AWS_DEFAULT_REGION","ap-southeast-2")
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("NEAT_SECRET_ARN", "neat")
os.environ.setdefault("GRAPH_SECRET_ARN", "graph")
