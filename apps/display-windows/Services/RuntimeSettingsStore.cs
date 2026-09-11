namespace ExhibitionClient.Services
{
    public static class RuntimeSettingsStore
    {
        public static RuntimeSettings Current { get; set; } = new RuntimeSettings();
    }
}
