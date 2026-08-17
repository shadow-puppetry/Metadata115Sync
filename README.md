# Metadata115Sync

MoviePilot V2 / V3 插件：只做 **NAS 本地元数据 → MP 已配置的 115** 单向增量同步。

## 功能

- 不使用 TMDB
- 不生成或刮削元数据
- 不上传视频文件
- 115 已存在的同路径文件直接跳过，不覆盖
- 本地不存在的文件不会从 115 删除
- 使用 MoviePilot 自带的 115 存储能力，不保存 115 Token/Cookie
- 支持多个“本地目录 → 115目录”映射
- 支持元数据扩展名和单文件大小限制
- 可选同步映射源目录的父目录元数据
- 支持手动 API 执行和周期同步

## 仓库结构

```text
package.v2.json
package.v3.json
plugins.v2/metadata115sync/__init__.py
plugins.v3/metadata115sync/__init__.py
tests/v2/metadata115sync/test_plugin.py
tests/v3/metadata115sync/test_plugin.py
```

## 映射示例

```text
/media/movies=/电影
/media/tv=/电视剧
```

左侧必须是 MoviePilot 容器内实际能访问到的本地路径，右侧是 115 中的目标路径。

## 默认元数据扩展名

```text
.nfo,.jpg,.jpeg,.png,.webp,.xml
```
