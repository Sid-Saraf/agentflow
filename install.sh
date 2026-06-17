#!/bin/bash
# Install agentflow into a target GitHub repo.
# Usage: ./install.sh owner/repo

set -e

REPO=${1:-$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null)}

if [ -z "$REPO" ]; then
  echo "Usage: ./install.sh owner/repo"
  exit 1
fi

echo "Installing agentflow into $REPO..."

# Clone target repo
TMP=$(mktemp -d)
gh repo clone "$REPO" "$TMP/target"
cd "$TMP/target"

# Copy workflows
mkdir -p .github/workflows
cp "$(dirname "$0")/templates/workflows/agent-builder.yml" .github/workflows/
cp "$(dirname "$0")/templates/workflows/agent-qa.yml" .github/workflows/

# Commit and push
git config user.name "agentflow"
git config user.email "agent@noreply.github.com"
git add .github/
git commit -m "chore: install agentflow agent pipeline" || echo "No changes to commit"
git push

# Create labels
python3 -c "
import sys
sys.path.insert(0, '$(dirname "$0")/agents')
from base import ensure_labels
ensure_labels('$REPO')
"

# Set OPENAI_API_KEY secret if not already set
if [ -n "$OPENAI_API_KEY" ]; then
  gh secret set OPENAI_API_KEY --body "$OPENAI_API_KEY" --repo "$REPO"
  echo "Secret OPENAI_API_KEY set."
else
  echo "⚠️  Set OPENAI_API_KEY manually: gh secret set OPENAI_API_KEY --repo $REPO"
fi

echo ""
echo "✅ agentflow installed in $REPO"
echo ""
echo "Usage:"
echo "  python agents/pm_agent.py --repo $REPO 'Your feature description'"
echo "  python agents/tech_spec.py --repo $REPO"
echo ""
echo "Then apply the 'ready-for-build' label to any issue to trigger the pipeline."
