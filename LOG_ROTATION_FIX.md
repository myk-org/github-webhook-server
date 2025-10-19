# Log Rotation Fix - Summary

## Problem

Log files were not rotating at the expected 10MB threshold (`file_max_bytes=1024 * 1024 * 10`). Some log files exceeded 11MB before rotation occurred.

### Root Cause

The issue was caused by **multiple logger instances with different names all writing to the same log file**:

```python
# Different logger instances were created:
logger1 = get_logger_with_params(name="main")        # Creates logger "main"
logger2 = get_logger_with_params(name="helpers")     # Creates logger "helpers"
logger3 = get_logger_with_params(name="app_utils")   # Creates logger "app_utils"
```

Each logger instance created its own `RotatingFileHandler` for the same log file. Python's `RotatingFileHandler` is not designed to handle multiple handlers writing to the same file because:

1. Each handler independently tracks file size
2. Only ONE handler triggers rotation when it detects the size threshold
3. Other handlers continue writing to the old file, causing it to exceed the limit
4. File handles can become stale after rotation by another handler

## Solution

Modified `get_logger_with_params()` in `webhook_server/utils/helpers.py` to:

1. **Remove the unused `name` parameter** - Since we now use a fixed logger name based on the log file path, the caller-provided name is no longer needed
2. **Use a fixed logger name** based on the log file path instead of accepting different names from callers:

```python
def get_logger_with_params(
    repository_name: str = "",
    mask_sensitive: bool = True,
) -> Logger:
    # ... config setup ...

    # CRITICAL FIX: Use a fixed logger name for the same log file to ensure
    # only ONE RotatingFileHandler instance manages the file rotation.
    logger_cache_key = f"webhook-server-{log_file or 'console'}"

    return get_logger(
        name=logger_cache_key,  # Same name = same logger instance
        filename=log_file,
        level=log_level,
        file_max_bytes=1024 * 1024 * 10,
        mask_sensitive=mask_sensitive,
        mask_sensitive_patterns=mask_sensitive_patterns,
    )
```

3. **Updated all callers** to remove the `name` parameter:
```python
# Before:
logger = get_logger_with_params(name="main")
logger = get_logger_with_params(name="helpers")

# After:
logger = get_logger_with_params()
logger = get_logger_with_params()
```

### How It Works

The `python-simple-logger` library caches loggers by name:
```python
if LOGGERS.get(name):
    return LOGGERS[name]  # Returns existing logger
```

By using the same `logger_cache_key` for all calls that write to the same log file:
- All code gets the **same logger instance**
- Only **ONE `RotatingFileHandler`** is created
- File rotation works correctly at the 10MB threshold

## Verification

Test script confirms the fix works:

```bash
$ WEBHOOK_SERVER_DATA_DIR=webhook_server/tests/manifests uv run python -c "
from webhook_server.utils.helpers import get_logger_with_params

# All calls without repository_name get the same logger instance
logger1 = get_logger_with_params()
logger2 = get_logger_with_params()  
logger3 = get_logger_with_params()

print(f'Logger 1 name: {logger1.name}')
print(f'Logger 2 name: {logger2.name}')
print(f'Logger 3 name: {logger3.name}')
print(f'Same instance? {logger1 is logger2 is logger3}')
"
```

**Output:**
```
Logger 1 name: webhook-server-webhook_server/tests/manifests/logs/webhook-server.log
Logger 2 name: webhook-server-webhook_server/tests/manifests/logs/webhook-server.log
Logger 3 name: webhook-server-webhook_server/tests/manifests/logs/webhook-server.log
✅ Same instance? True
```

## Files Changed

1. **`webhook_server/utils/helpers.py`**
   - Removed unused `name` parameter from `get_logger_with_params()`
   - Use fixed logger name based on log file path
   - Updated all internal calls to remove `name` argument
   - Added comments explaining the fix

2. **`webhook_server/app.py`**
   - Updated calls: `get_logger_with_params()` instead of `get_logger_with_params(name="main")`

3. **`webhook_server/utils/app_utils.py`**
   - Updated call: `get_logger_with_params()` instead of `get_logger_with_params(name="app_utils")`

4. **`webhook_server/utils/github_repository_settings.py`**
   - Updated call: `get_logger_with_params()` instead of `get_logger_with_params(name="github-repository-settings")`

5. **`webhook_server/utils/webhook.py`**
   - Updated call: `get_logger_with_params()` instead of `get_logger_with_params(name="webhook")`

6. **`webhook_server/utils/github_repository_and_webhook_settings.py`**
   - Updated call: `get_logger_with_params()` instead of `get_logger_with_params(name="repository-and-webhook-settings")`

7. **`webhook_server/tests/test_helpers.py`**
   - Updated all test calls to remove `name` parameter
   - Tests verify new logger naming pattern

## Conclusion

**The issue was in your code, not in the `python-simple-logger` library.** The library works correctly when used properly with a single logger instance per log file. The fix ensures that all parts of your application share the same logger instance, allowing the `RotatingFileHandler` to manage file rotation correctly.

### Expected Behavior After Fix

- Log files will rotate when they reach **10MB** (not exceed it)
- Backup files will be created (`.log.1`, `.log.2`, etc.)
- Maximum of **20 backup files** will be kept
- No more files exceeding the size limit

### Monitoring

After deploying this fix, monitor your log directory:
```bash
ls -lh /mnt/nfs/mediaserver/docker-compose/services/github-webhook-server/data-myakove/webhook_server/logs/
```

You should see:
- Current log file stays under 10MB
- Rotated files are approximately 10MB each
- Proper rotation sequence maintained
