# Metadata115Sync

MoviePilot V3.0.0 插件。

## 功能

**只做一件事：NAS 本地已有的元数据 → MoviePilot 已配置的 115。**

- 不使用 TMDB
- 不刮削
- 不生成元数据
- 不修改本地文件
- 不从 115 下载文件
- 不删除任何文件
- 不上传视频文件
- 115 已存在同路径同文件名时跳过，不覆盖
- 使用 MoviePilot 自己的 115 存储配置和存储链，不保存 115 Cookie/Token

## V3 结构

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

## 配置示例

```text
/media/movies=/电影
/media/tv=/电视剧
```

例如本地：

```text
/media/movies/Avatar (2009)/poster.jpg
```

会检查 115：

```text
/电影/Avatar (2009)/poster.jpg
```

115 已有则跳过；没有才上传。

默认元数据扩展名：`.nfo,.jpg,.jpeg,.png,.webp,.xml`

默认大小限制：20 MB

第一次使用建议只配置一个小测试目录，并关闭自动同步，确认结果后再开启定时同步。
