class ExtractionError(Exception):
    """An extractor could not produce usable text. The message says why.

    Shared by the Extraction Server (which raises it) and the Reader (which
    raises it again from the server's error reply), so it lives in the domain.
    """
