# Metadata115Sync V2

MoviePilot V2 专用插件。

## 功能

只做一件事：

> 本地已有的元数据文件，115 中不存在 → 上传到 MoviePilot 已配置的 115。

115 已存在 → 跳过。

不会：

- 使用 TMDB
- 刮削或生成元数据
- 上传视频文件
- 覆盖 115 已存在文件
- 删除 115 文件
- 从 115 下载文件

## 仓库结构

```text
package.v2.json
plugins.v2/
└── metadata115sync/
    └── __init__.py
```

## 配置

本地目录 → 115目录映射每行一个，例如：

```text
/media/movies=/电影
/media/tv=/电视剧
```

左侧必须是 MoviePilot 容器内实际可访问的本地路径。

115 使用 MoviePilot 已配置的 `u115` 存储，不需要在插件里填写 Cookie 或 Token。
