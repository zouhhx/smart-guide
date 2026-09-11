# 随仓库保留的二进制构建依赖

| 文件 | 大小（字节） | SHA-256 |
| --- | ---: | --- |
| app/libs/Codec.aar | 1520055 | 45DC45BF43783D405465C0331922A9150956985CE38541A1E6622DC22C9B1BDD |
| app/libs/SparkChain.aar | 4511062 | 33CD2720A5595AC1CF436D3230D621C0A385759555F6126B9F82DD6D6736F8DE |
| gradle/wrapper/gradle-wrapper.jar | 43739 | 9687BF4E8BEB2C293CFD1393CBF2854EF68F921757302116033F476289ABF7B0 |

这些文件是现有工程的必要输入，不属于 build 输出，已纳入 Git。最大文件约 4.5 MB，本地基线未启用 Git LFS；后续服从公司 GitLab 的文件限制。

Codec/SparkChain 是供应商 SDK，不属于公司自研源码；本次保留已有文件，不新增许可证授权。仅在公司允许的私有项目范围内托管，后续公开或再分发需由负责人核实供应商许可。依赖版本保持原值，没有在整理仓库时升级。
