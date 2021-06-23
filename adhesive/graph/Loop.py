class Loop:
    def __init__(self, loop_expression: str, parallel: bool) -> None:
        self.loop_expression = loop_expression
        self.parallel = parallel
        self.input_collection = None
        self.input_element = None
        self.output_collection = None
        self.output_element = None
