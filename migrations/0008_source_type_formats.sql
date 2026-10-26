-- 0008：documents.source_type 放宽到契约里的文件格式枚举
--
-- 0001 只允许 pdf/word/excel/ticket/drawing/video/audio，而契约（openapi 的
-- DocumentDetail.format）与前端用的是 docx/txt/md/xlsx。结果 Word/Markdown/TXT
-- 上传时被 documents_source_type_chk 挡住（500），而不是给业务提示。
--
-- 这里保留旧取值以兼容历史行，同时把契约里的取值补齐；
-- 新增格式时请同步 contracts/openapi.yaml 的 format enum。

alter table documents drop constraint if exists documents_source_type_chk;

alter table documents add constraint documents_source_type_chk check (
    source_type in (
        'pdf',
        'docx', 'word',
        'md', 'txt', 'csv',
        'excel', 'xlsx',
        'ticket', 'ticketpkg',
        'drawing', 'drawingindex',
        'video', 'audio'
    )
);
