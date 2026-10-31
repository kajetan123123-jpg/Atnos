"""Read-only browser review of canonical orders, without starting the desktop lifecycle."""
import os
from pathlib import Path

os.environ.update(ATNOS_ENV_FILE='E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/config/.env',
                  TEMP='E:/Atnos/Temp', TMP='E:/Atnos/Temp')
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from atnos.core.config import get_settings
from atnos.api.routes.production import router

app = FastAPI()
app.state.settings = get_settings().model_copy(update={
    'runtime_dir': Path('E:/Atnos/App/reports/readiness-20260909/story-browser/runtime')})
app.add_middleware(CORSMiddleware, allow_origins=['http://127.0.0.1:3000'], allow_methods=['GET'], allow_headers=['*'])


@app.middleware('http')
async def read_only(request: Request, call_next):
    if request.method not in {'GET', 'OPTIONS'}:
        return JSONResponse({'detail': 'Read-only review harness; mutations are disabled.'}, status_code=405)
    return await call_next(request)


@app.get('/health')
def health():
    return {'status': 'ok', 'scope': 'read_only_story_workspace_review'}


app.include_router(router)

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8000)
