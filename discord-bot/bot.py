import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from platforms.discord.adapter import main
main()
