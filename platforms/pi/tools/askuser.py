def askuser(command, memory, local_state):
    import sys

    print("READING RAW CHARACTERS")

    while True:
        c = sys.stdin.read(1)
        print("CHAR =", repr(c))