from .custom_logger import CustomLogger

# One shared logger for the whole project
GLOBAL_LOGGER = CustomLogger().get_logger("multi_doc_chat")