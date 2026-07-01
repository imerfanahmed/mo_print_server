import sys
from ui import PrintServerUI

if __name__ == "__main__":
    app = PrintServerUI()
    # Trigger withdraw instantly if start-on-boot is set and it was launched automatically
    if len(sys.argv) > 1 and sys.argv[1] == "--hide":
        app.withdraw_window()
    app.mainloop()