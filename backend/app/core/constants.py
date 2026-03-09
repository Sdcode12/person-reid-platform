from __future__ import annotations


class Color:
    BLACK = "black"
    WHITE = "white"
    GRAY = "gray"
    RED = "red"
    ORANGE = "orange"
    YELLOW = "yellow"
    GREEN = "green"
    BLUE = "blue"
    PURPLE = "purple"
    BROWN = "brown"
    UNKNOWN = "unknown"

    ALL = [
        BLACK,
        WHITE,
        GRAY,
        RED,
        ORANGE,
        YELLOW,
        GREEN,
        BLUE,
        PURPLE,
        BROWN,
        UNKNOWN,
    ]

    NEIGHBORS = {
        RED: [RED, ORANGE],
        ORANGE: [ORANGE, RED, BROWN],
        YELLOW: [YELLOW, GREEN, BROWN],
        GREEN: [GREEN, YELLOW],
        BLUE: [BLUE, PURPLE],
        PURPLE: [PURPLE, BLUE, RED],
        BROWN: [BROWN, ORANGE, YELLOW],
        BLACK: [BLACK, GRAY],
        WHITE: [WHITE, GRAY],
        GRAY: [GRAY, BLACK, WHITE],
    }
