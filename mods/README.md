# mods 目录

把 mod 压缩包放进这个文件夹，就能在界面上点「⚡ 一键离线安装」，全程不联网。

## 文件命名要求

必须是 Thunderstore 的标准命名：`作者-Mod名-版本.zip`

```
BepInEx-BepInExPack-5.4.2305.zip
clay-BetterMap-1.0.5.zip
Zehs-REPOLib-4.2.0.zip
```

从 [Thunderstore](https://thunderstore.io/c/repo/) 下载的包天然符合这个格式，直接丢进来即可。

## 为什么这个文件夹是空的

本仓库**不包含任何 mod 文件**。mod 的版权归各自作者所有，重新分发需要遵守各作者的授权条款。
所以 `*.zip` 已在 `.gitignore` 中被排除，请自行下载。

推荐做法：

1. 在 [Thunderstore](https://thunderstore.io/c/repo/) 上找到想要的 mod
2. 下载 zip（或用界面里的「安装勾选的 Mod」在线安装，会自动处理依赖）
3. 想离线使用，就把 zip 丢进这里

## 建议至少包含

- `BepInEx-BepInExPack-*.zip` —— 所有 mod 的前置框架，工具会自动优先安装它
