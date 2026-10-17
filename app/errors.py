class AppError(Exception):
    code = "invalid"
    status = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFound(AppError):
    code = "not_found"
    status = 404


class PermissionDenied(AppError):
    code = "permission_denied"
    status = 403


class ModelError(AppError):
    code = "model_error"
    status = 502
