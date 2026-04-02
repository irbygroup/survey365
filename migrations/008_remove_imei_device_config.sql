DELETE FROM config
WHERE key IN (
    'original_imei',
    'generated_imei',
    'generated_model',
    'generated_date',
    'imei_api_token',
    'imei_max_retries',
    'imei_models',
    'check_lost_device',
    'check_verizon',
    'check_tmobile',
    'check_blacklist'
);
