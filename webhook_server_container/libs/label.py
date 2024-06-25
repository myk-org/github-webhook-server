class Label:
    def __init__(self, name, color, description):
        self.name = name
        self.color = color
        self.description = description

    def __str__(self):
        return f"Label(name={self.name}, color={self.color}, description={self.description})"
