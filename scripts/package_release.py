"""Allowlisted source package. Never include API keys, data, or unreviewed artifacts."""
from pathlib import Path
import zipfile
import os
ROOT=Path(__file__).resolve().parents[1]
TOP={'README.md','requirements.txt','requirements.lock.txt','.env.example','.gitignore','pytest.ini','setup.ps1','start.ps1','test.ps1','Dockerfile','compose.yaml','compose.cloudflare.yaml','compose.server.yaml','.dockerignore'}
DIRS={'backend','frontend','scripts','docs','deploy','samples','tests'}
EXCLUDED={'node_modules','dist','__pycache__','.pytest_cache','results','private','.wrangler'}

def package(target=None):
    target=Path(target) if target else ROOT/'output/zhicai-source.zip'
    target.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        paths=[ROOT/name for name in TOP]
        for directory in DIRS:
            for base,dirs,names in os.walk(ROOT/directory):
                dirs[:]=[d for d in dirs if d not in EXCLUDED]
                paths.extend(Path(base)/name for name in names)
        for path in paths:
            rel=path.relative_to(ROOT)
            if not path.is_file() or any(part in EXCLUDED for part in rel.parts) or path.name=='wrangler.jsonc':continue
            if (len(rel.parts)==1 and rel.name in TOP) or (len(rel.parts)>1 and rel.parts[0] in DIRS):
                if path.suffix.lower() not in ('.py','.js','.mjs','.vue','.css','.html','.json','.jsonc','.md','.yaml','.txt','.ps1','.sh','.conf','.service','.timer') and path.name!='Caddyfile' and path.name not in TOP and str(rel).replace('\\','/')!='backend/resources/standard_output_template.xlsx':continue
                archive.write(path,str(rel))
    return target

if __name__=='__main__':print(package())
