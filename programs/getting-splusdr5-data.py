import pandas as pd
import splusdata
import os
import logging
import time
from getpass import getpass
from contextlib import contextmanager
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import requests
from astropy.table import Table
from astropy.io import fits
from concurrent.futures import ThreadPoolExecutor, as_completed

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("splus_query.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('SPLUS_QUERY')

# Configuración de constantes
MAX_RETRIES = 5
RETRY_DELAY = 20  # Aumentar tiempo de espera
MAX_WORKERS = 4
TIMEOUT = 30  # Timeout para consultas
COLUMN_TYPES = {
    'RA': 'float64',
    'DEC': 'float64',
    'X': 'float32',
    'Y': 'float32',
    'PETRO_RADIUS': 'float32',
    'KRON_RADIUS': 'float32',
    'r_PStotal': 'float32',
    'e_r_PStotal': 'float32',
    'g_PStotal': 'float32',
    'e_g_PStotal': 'float32',
    'i_PStotal': 'float32',
    'e_i_PStotal': 'float32',
    'u_PStotal': 'float32',
    'e_u_PStotal': 'float32',
    'z_PStotal': 'float32',
    'e_z_PStotal': 'float32',
    'PROB_STAR': 'float32',
    'PROB_QSO': 'float32',
    'PROB_GAL': 'float32',
    'CLASS': 'int16',
    'SEX_FLAGS_DET': 'int8',
    'SEX_FLAGS_r': 'int8',
    'SEX_FLAGS_i': 'int8',
    'SEX_FLAGS_J0660': 'int8'
}

@contextmanager
def create_connection(username=None, password=None):
    """Conexión segura con manejo mejorado de errores"""
    session = requests.Session()
    retry_strategy = Retry(
        total=MAX_RETRIES,
        backoff_factor=2,
        status_forcelist=[500, 502, 503, 504, 429]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount('https://', adapter)
    
    try:
        conn = splusdata.Core(
            username=username or os.getenv('SPLUS_USERNAME'),
            password=password or os.getenv('SPLUS_PASSWORD')
        )
        conn.session = session
        yield conn
    except Exception as e:
        logger.error(f"Error de conexión: {str(e)}")
        raise
    finally:
        session.close()

def validate_field(conn, field):
    """Validación mejorada con sintaxis ADQL correcta"""
    try:
        test_query = f"SELECT TOP 1 Field FROM idr5.idr5_dual WHERE Field = '{field}'"
        result = conn.query(query=test_query, timeout=TIMEOUT)
        return result is not None and not result.empty
    except Exception as e:
        logger.error(f"Error validando {field}: {str(e)}")
        return False

def build_query(field):
    """Consulta ADQL verificada"""
    return f"""
    SELECT 
        dual.Field, dual.ID, dual.RA, dual.DEC,
        dual.X, dual.Y,
        dual.PETRO_RADIUS, dual.KRON_RADIUS,
        dual.s2n_DET_PStotal, dual.s2n_r_PStotal, 
        dual.s2n_J0660_PStotal, dual.s2n_i_PStotal, 
        dual.SEX_FLAGS_DET, dual.SEX_FLAGS_r, 
        dual.SEX_FLAGS_i, dual.SEX_FLAGS_J0660,
        dual.r_PStotal, dual.e_r_PStotal,
        dual.g_PStotal, dual.e_g_PStotal,
        dual.i_PStotal, dual.e_i_PStotal,
        dual.u_PStotal, dual.e_u_PStotal,
        dual.z_PStotal, dual.e_z_PStotal,
        dual.j0378_PStotal, dual.e_j0378_PStotal,
        dual.j0395_PStotal, dual.e_j0395_PStotal,
        dual.j0410_PStotal, dual.e_j0410_PStotal,
        dual.j0430_PStotal, dual.e_j0430_PStotal,
        dual.j0515_PStotal, dual.e_j0515_PStotal,
        dual.j0660_PStotal, dual.e_j0660_PStotal,
        dual.j0861_PStotal, dual.e_j0861_PStotal,
        psf.r_psf, psf.e_r_psf,
        psf.g_psf, psf.e_g_psf,
        psf.i_psf, psf.e_i_psf,
        psf.u_psf, psf.e_u_psf,
        psf.z_psf, psf.e_z_psf,
        psf.j0378_psf, psf.e_j0378_psf,
        psf.j0395_psf, psf.e_j0395_psf,
        psf.j0410_psf, psf.e_j0410_psf,
        psf.j0430_psf, psf.e_j0430_psf,
        psf.j0515_psf, psf.e_j0515_psf,
        psf.j0660_psf, psf.e_j0660_psf,
        psf.j0861_psf, psf.e_j0861_psf,
        sgq.CLASS, sgq.PROB_STAR, sgq.PROB_QSO, sgq.PROB_GAL
    FROM idr5.idr5_dual AS dual
    LEFT JOIN idr5.idr5_psf AS psf USING(ID)
    LEFT JOIN idr5_vacs.idr5_sqg AS sgq USING(ID)
    WHERE 
        e_J0395_PStotal <= 0.3 
        AND e_J0410_PStotal <= 0.3 
        AND e_J0430_PStotal <= 0.3 
        AND e_g_PStotal <= 0.3  
        AND e_J0515_PStotal <= 0.3 
        AND e_r_PStotal <= 0.3 
        AND e_J0660_PStotal <= 0.3
        AND e_i_PStotal <= 0.3 
        AND e_J0861_PStotal <= 0.3 
        AND e_z_PStotal <= 0.3
        AND sgq.CLASS = 1
        AND dual.Field = '{field}'
    """

def process_field(conn, field, output_dir):
    """Procesamiento robusto con manejo de timeout"""
    for attempt in range(MAX_RETRIES):
        try:
            logger.info(f"Procesando {field} | Intento {attempt+1}")
            query = build_query(field)
            df = conn.query(query, timeout=TIMEOUT).to_pandas()
            
            if not df.empty:
                df = df.astype({k: v for k, v in COLUMN_TYPES.items() if k in df.columns})
                output_file = os.path.join(output_dir, f"{field}.parquet")
                df.to_parquet(output_file)
                return output_file
            return None
        except Exception as e:
            logger.warning(f"Intento {attempt+1} fallido: {str(e)}")
            time.sleep(RETRY_DELAY * (2 ** attempt))
    logger.error(f"Campo {field} no procesado")
    return None

def main(test_mode=False):
    start_time = time.time()
    output_dir = "splus_results"
    os.makedirs(output_dir, exist_ok=True)
    
    try:
        with create_connection() as conn:
            fields = pd.read_csv("iDR5_pointings.csv")
            if test_mode:
                fields = fields.sample(20)
                logger.info("Modo prueba con 20 campos")
            
            logger.info("Validando campos...")
            valid_fields = [field for field in fields['iDR5_Field_Name'] if validate_field(conn, field)]
            logger.info(f"Campos válidos: {len(valid_fields)}/{len(fields)}")
            
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {executor.submit(process_field, conn, f, output_dir): f for f in valid_fields}
                processed_data = []
                
                for future in as_completed(futures):
                    field = futures[future]
                    try:
                        result = future.result()
                        if result:
                            df = pd.read_parquet(result)
                            processed_data.append(df)
                            logger.info(f"{field}: {len(df)} objetos")
                    except Exception as e:
                        logger.error(f"Error final en {field}: {str(e)}")
            
            if processed_data:
                final_df = pd.concat(processed_data, ignore_index=True)
                logger.info(f"Total de objetos: {len(final_df):,}")
                
                astropy_table = Table.from_pandas(final_df)
                astropy_table.meta.update({
                    'OBSERVER': 'LUIS',
                    'TELESCOP': 'S-PLUS',
                    'VERSION': 'iDR5',
                    'CREATED': time.strftime('%Y-%m-%d %H:%M:%S')
                })
                
                output_file = f"splus_data_{time.strftime('%Y%m%d%H%M')}.fits"
                astropy_table.write(output_file, format='fits', overwrite=True)
                logger.info(f"Archivo FITS creado: {output_file}")
            else:
                logger.warning("No hay datos para procesar")
                
    except Exception as e:
        logger.error(f"Error crítico: {str(e)}")
    finally:
        logger.info(f"Tiempo total: {(time.time()-start_time)/60:.1f} minutos")

if __name__ == "__main__":
    main(test_mode=True)
