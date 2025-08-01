const About = () => {
    return (
      <div className="text-center">
        <h2 className="text-2xl font-bold">About RUCKUS.Tools</h2>
        <p className="text-gray-600">Just some tools to help out Ruckus ONE dpeloyments</p>
        
        <h3>Other ideas</h3>
        <ul className="list-disc list-inside text-gray-600">
          <li>Replace physical AP - need to pull old AP-level overrides from old and apply to new AP</li>
          <li>Apply multiple templates at once - aka master templates</li>
          <li>Selecting more than 100 items at a time for certain configs</li>
          <li>Bulk SW moves (AP already being worked)</li>
          <li>Export AP list to CSV - edit CSV - upload to apply setting changes</li>
          <li>Per Unit SSID generator</li>
          <li>SZ firmware/AP compatibility matrix and upgrade path tool</li>
          <li>PoE vs AP Model functionality based on data sheet power notes</li>
        </ul>
      </div>
    );
  };
  
  export default About;
  