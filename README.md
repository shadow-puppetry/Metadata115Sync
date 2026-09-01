# Metadata115Sync

MoviePilot V3 插件。

## 功能

只做单向同步：

**NAS 本地元数据 → MoviePilot 已配置的 115 存储**

- 本地有、115 没有：上传
- 115 已有：跳过
- 不覆盖 115 已有文件
- 不删除本地或115文件
- 不下载115文件
- 不调用 TMDB
- 使用 MoviePilot 自带 StorageChain，因此复用 MP 已配置的 115 登录

## 正确 V3 目录

```text
Metadata115Sync/
├── package.v3.json
├── plugins.v3/
│   └── metadata115sync/
│       └── __init__.py
└── tests/
    └── v3/
        └── metadata115sync/
            └── test_plugin.py
```

## 配置

目录映射每行一个：

```text
/media/movies=/电影
/media/tv=/电视剧
```

默认扩展名：

```text
.nfo,.jpg,.jpeg,.png,.webp,.xml
```

默认最大文件：

20 MB

默认115存储名称：

`u115`

第一次使用建议关闭自动同步，手动执行一次确认路径正确。
